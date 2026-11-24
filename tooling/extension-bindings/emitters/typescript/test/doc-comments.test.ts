import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { dependencyPlan } from "../src/dependencies.js";
import { loadModel } from "../src/model.js";
import { buildPlan, type EmissionPlan } from "../src/plan.js";
import { emitPackage } from "../src/source.js";
import { apiSurface } from "../src/verification.js";
import { buildCase, modelPath, runTsc, targetFor, temporaryDirectory } from "./support.js";
import { readFileSync, symlinkSync } from "node:fs";

const plain = `export class Declaration {}
export interface ApiField {
  readonly __rc: "api";
}
export enum Method {
  Get = "GET",
}
export class Spec {
  readonly uri: string;
  readonly version?: string;
  constructor(fields: { readonly uri: string; readonly version?: string }) {
    this.uri = fields.uri;
    this.version = fields.version;
  }
}
export type Operations = ReadonlyArray<Spec>;
export function api(...fields: ApiField[]): Declaration {
  void fields;
  return new Declaration();
}
`;

const documented = `/** Generated bindings. */
/** The inert result of a declaration. */
export class Declaration {}
/** Field contract for api declarations. */
export interface ApiField {
  /** Marker. */
  readonly __rc: "api";
}
/** HTTP method. */
export enum Method {
  /** The GET method. */
  Get = "GET", // trailing note
}
/** An OpenAPI document reference. */
export class Spec {
  /** Catalog URI of the document. */
  readonly uri: string;
  /** Document version. */
  readonly version?: string;
  /** Creates a Spec. */
  constructor(fields: {
    /** Catalog URI of the document. */
    readonly uri: string;
    /** Document version. */
    readonly version?: string /* optional */
  }) {
    this.uri = fields.uri;
    this.version = fields.version;
  }
}
/** Operations list. */
export type Operations = ReadonlyArray</* item */ Spec>;
/** Declares an api Condition. */
export function api(/** fields */ ...fields: ApiField[]): Declaration {
  void fields;
  return new Declaration();
}
`;

function surface(source: string): string[] {
  const directory = mkdtempSync(join(tmpdir(), "rc-doc-comments-"));
  try {
    mkdirSync(join(directory, "src"));
    writeFileSync(join(directory, "src/bindings.ts"), source);
    return apiSurface(directory);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

test("doc comments never change the API surface used for release compatibility", () => {
  const expected = surface(plain);
  assert.ok(expected.some((entry) => entry.startsWith("constructor|Spec|")));
  assert.deepEqual(surface(documented), expected);
});

test("extension descriptions become JSDoc in the generated bindings", async () => {
  const workspace = temporaryDirectory();
  try {
    const built = await buildCase("14-documented-extension", workspace);
    const source = readFileSync(join(built.directory, "src/bindings.ts"), "utf-8");
    for (const expected of [
      // Extension summary as the package documentation.
      "@packageDocumentation",
      "Common integrations a workload depends on",
      // Kind description on the declaration function.
      "The workload calls another service through a network API",
      // Interface type description on the interface class.
      "A key/value store, such as Redis or Memcached.",
      // Interface field meaning, then the JSON Schema rule.
      "The cache engine the workload needs.",
      "Optional. When present, must be one of the defined engine values.",
      // Field value descriptions on enum members.
      "Redis, an in-memory key/value store.",
      "Read a resource.",
    ]) {
      assert.ok(source.includes(expected), `generated bindings are missing ${JSON.stringify(expected)}`);
    }
    // npm shows the package.json description: the extension summary on one line.
    const manifest = JSON.parse(readFileSync(join(built.directory, "package.json"), "utf-8"));
    assert.match(manifest.description, /^Common integrations a workload depends on: network APIs \(HTTP and SMTP\)/);
    assert.deepEqual(Object.keys(manifest).slice(0, 4), ["//", "name", "version", "description"]);
    // Field docs also appear inside the constructor's fields type, where editors show them.
    assert.match(source, /constructor\(\s*fields: \{[^}]*\/\*\*[^}]*The cache engine the workload needs\./s);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("every README example compiles against the generated package", async () => {
  const workspace = temporaryDirectory();
  try {
    const built = await buildCase("14-documented-extension", workspace);
    const readme = readFileSync(join(built.directory, "README.md"), "utf-8");
    const examples = [...readme.matchAll(/```ts\n([\s\S]*?)```/g)].map((match) => match[1]!);
    assert.ok(examples.length >= 5, `expected the fixture's examples, found ${examples.length}`);
    examples.forEach((example, index) => {
      const source = example.replace(
        `from ${JSON.stringify(built.plan.target.packageName)};`,
        'from "./bindings.js";',
      );
      writeFileSync(join(built.directory, `src/_example_${index}.ts`), source);
    });
    const compiled = runTsc(built.directory, true);
    assert.equal(compiled.status, 0, compiled.output);
    // The same code is in the JSDoc of the declaration functions.
    const bindings = readFileSync(join(built.directory, "src/bindings.ts"), "utf-8");
    assert.ok(bindings.includes(" * cache(new KeyValue({ engine: KeyValueEngine.Redis }));"));
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

function exportedNames(plan: EmissionPlan): string[] {
  return [
    ...plan.types.map((item) => `type ${item.name}`),
    ...plan.declarations.map((item) => `function ${item.function}`),
  ].sort();
}

test("a dependent package's README examples call the dependency and compile against both packages", async () => {
  const workspace = temporaryDirectory();
  try {
    // The dependency is case 14's extension, so its own package is case 14's.
    const dependency = await buildCase("14-documented-extension", workspace);
    const built = runTsc(dependency.directory, false);
    assert.equal(built.status, 0, built.output);
    const dependencyName = dependency.plan.target.packageName;
    mkdirSync(join(workspace, "node_modules", "@rc"), { recursive: true });
    symlinkSync(dependency.directory, join(workspace, "node_modules", dependencyName));

    const name = "15-documented-dependent";
    const model = loadModel(modelPath(name));
    const plan = buildPlan(
      model,
      targetFor(name, model, new Map([[dependency.plan.target.rootExtension, dependencyName]])),
    );
    // Names planned from the dependent model match the dependency package's own.
    const derived = dependencyPlan(plan, model, dependency.plan.target.rootExtension);
    assert.ok(derived, "the dependency package could not be planned from the dependent model");
    assert.deepEqual(exportedNames(derived), exportedNames(dependency.plan));

    const directory = join(workspace, "pkg", name);
    await emitPackage(plan, model, directory);
    const readme = readFileSync(join(directory, "README.md"), "utf-8");
    const examples = [...readme.matchAll(/```ts\n([\s\S]*?)```/g)].map((match) => match[1]!);
    assert.ok(examples.length >= 2, `expected the fixture's examples, found ${examples.length}\n${readme}`);
    examples.forEach((example, index) => {
      writeFileSync(
        join(directory, `src/_example_${index}.ts`),
        example.replace(`from ${JSON.stringify(plan.target.packageName)};`, 'from "./bindings.js";'),
      );
    });
    const compiled = runTsc(directory, true);
    assert.equal(compiled.status, 0, compiled.output);

    for (const expected of [
      "## KeyValueConfiguration",
      "`KeyValueConfiguration` sets the `configuration` field of `cache` Conditions with interface type `key_value`.",
      `import { KeyValue, KeyValueEngine, cache } from ${JSON.stringify(dependencyName)};`,
      `import { KeyValueConfiguration, KeyValueConfigurationEnvItem } from ${JSON.stringify(plan.target.packageName)};`,
      "In a Profile, this declares:",
    ]) {
      assert.ok(readme.includes(expected), `README is missing ${JSON.stringify(expected)}\n${readme}`);
    }
    // The condition field's JSDoc shows the same code.
    const bindings = readFileSync(join(directory, "src/bindings.ts"), "utf-8");
    assert.ok(bindings.includes(" *   cache(") || bindings.includes(" * cache("), bindings);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

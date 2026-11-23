import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { apiSurface } from "../src/verification.js";
import { buildCase, temporaryDirectory } from "./support.js";
import { readFileSync } from "node:fs";

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
    // Field docs also appear inside the constructor's fields type, where editors show them.
    assert.match(source, /constructor\(\s*fields: \{[^}]*\/\*\*[^}]*The cache engine the workload needs\./s);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

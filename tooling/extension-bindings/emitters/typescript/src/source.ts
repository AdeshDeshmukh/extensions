import { existsSync, lstatSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import * as prettier from "prettier";

import { fail } from "./diagnostics.js";
import type { Model } from "./model.js";
import type { EmissionPlan, TypePlan } from "./plan.js";
import { header, renderResources } from "./metadata.js";
import { JSON_VALUE_ALIAS_RHS } from "./plan.js";
import {
  conditionFieldDocs,
  dedent,
  renderExamples,
  splitDocumentation,
  type ConditionFieldDoc,
  type RenderedExample,
} from "./examples.js";
import { readTemplate } from "./templates.js";

function sourceHeader(model: Model): string {
  return `// ${header(model)}`;
}

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

export function exportedNames(plan: EmissionPlan): string[] {
  const names = new Set<string>(plan.declarations.length > 0 ? ["Declaration"] : []);
  for (const item of plan.types) names.add(item.name);
  for (const item of [...plan.declarations, ...plan.importedDeclarations]) {
    names.add(item.function);
    names.add(item.protocol);
  }
  if (plan.usesJsonValue) names.add("JSONValue");
  return [...names].sort(compare);
}

// Examples rendered for the package being emitted. renderSources sets this
// synchronously around bindingsSource, so concurrent emissions cannot mix it.
let currentExamples: ReadonlyMap<string, RenderedExample> = new Map();

function proseLines(text: string, indent: string): string[] {
  const width = 96 - indent.length;
  const lines: string[] = [];
  text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.split(/\s+/).filter(Boolean))
    .filter((words) => words.length > 0)
    .forEach((words, index) => {
      if (index > 0) lines.push(`${indent} *`);
      let line = "";
      for (const word of words) {
        const escaped = word.replace(/\*\//g, "*\\/");
        if (line !== "" && line.length + 1 + escaped.length > width) {
          lines.push(`${indent} * ${line}`);
          line = escaped;
        } else {
          line = line === "" ? escaped : `${line} ${escaped}`;
        }
      }
      if (line !== "") lines.push(`${indent} * ${line}`);
    });
  return lines;
}

function codeLines(language: string, code: string, indent: string): string[] {
  return [
    `${indent} * \`\`\`${language}`,
    ...code.split("\n").map((line) => `${indent} * ${line.replace(/\*\//g, "*\\/")}`.trimEnd()),
    `${indent} * \`\`\``,
  ];
}

// docComment renders extension documentation as JSDoc. Descriptions come from
// the extension's vocabulary and JSON Schema; text without one renders nothing.
// Example Conditions become TypeScript code blocks. Comments never change the
// recorded API surface (see verification.ts).
export function docComment(text: string | undefined, indent = ""): string[] {
  const body: string[] = [];
  for (const segment of splitDocumentation((text ?? "").trim())) {
    const lines =
      segment.condition === undefined
        ? proseLines(segment.text!, indent)
        : currentExamples.has(segment.source!)
          ? codeLines("ts", currentExamples.get(segment.source!)!.code, indent)
          : codeLines("yaml", dedent(segment.source!).trim(), indent);
    if (lines.length === 0) continue;
    if (body.length > 0) body.push(`${indent} *`);
    body.push(...lines);
  }
  if (body.length === 0) return [];
  return [`${indent}/**`, ...body, `${indent} */`];
}

function renderObject(item: TypePlan): string[] {
  const lines: string[] = [];
  const implement = item.marker !== null ? ` implements ${item.marker.protocol}` : "";
  lines.push(...docComment(item.description));
  lines.push(`export class ${item.name}${implement} {`);
  for (const field of item.fields) {
    lines.push(...docComment(field.description, "  "));
    lines.push(`  readonly ${field.name}${field.required ? "" : "?"}: ${field.annotation};`);
  }
  if (item.fields.length > 0) {
    const documented = item.fields.some((field) => field.description);
    const parameter = documented
      ? `\n${item.fields
          .map((field) =>
            [
              ...docComment(field.description, "    "),
              `    readonly ${field.name}${field.required ? "" : "?"}: ${field.annotation};`,
            ].join("\n"),
          )
          .join("\n")}\n  `
      : ` ${item.fields
          .map((field) => `readonly ${field.name}${field.required ? "" : "?"}: ${field.annotation}`)
          .join("; ")} `;
    const optional = item.fields.every((field) => !field.required) ? " = {}" : "";
    lines.push("", `  constructor(fields: {${parameter}}${optional}) {`);
    for (const field of item.fields) lines.push(`    this.${field.name} = fields.${field.name};`);
    lines.push("  }");
  }
  if (item.marker !== null) {
    lines.push("", `  ${item.marker.method}(): void {}`);
  }
  lines.push("}");
  return lines;
}

function bindingsSource(plan: EmissionPlan, model: Model): string {
  const lines: string[] = [sourceHeader(model), ""];
  if (plan.packageDescription) {
    lines.push(
      ...docComment(
        `Runtime Conditions defined by ${plan.target.rootExtension}.\n\n${plan.packageDescription}`,
      ).slice(0, -1),
      " *",
      " * @packageDocumentation",
      " */",
      "",
    );
  }
  const imports = new Map<string, Set<string>>();
  const importedTypes = new Set<string>();
  for (const item of plan.importedDeclarations) {
    if (item.providerPackage === null) {
      fail(
        "model",
        "RCT1011",
        item.coordinate,
        "imported declaration has no direct dependency provider",
      );
    }
    const names = imports.get(item.providerPackage) ?? new Set<string>();
    names.add(item.function);
    names.add(item.protocol);
    importedTypes.add(item.protocol);
    imports.set(item.providerPackage, names);
  }
  for (const provider of [...imports.keys()].sort(compare)) {
    const specifiers = [...imports.get(provider)!]
      .sort(compare)
      .map((name) => (importedTypes.has(name) ? `type ${name}` : name));
    lines.push(`import { ${specifiers.join(", ")} } from ${JSON.stringify(provider)};`);
  }
  if (imports.size > 0) {
    const reexports = [...imports.values()].flatMap((names) => [...names]).sort(compare);
    lines.push(
      "",
      `export { ${reexports.map((name) => (importedTypes.has(name) ? `type ${name}` : name)).join(", ")} };`,
    );
  }
  if (plan.declarations.length > 0) lines.push("", "export class Declaration {}");
  for (const declaration of [...plan.declarations].sort((a, b) =>
    compare(a.protocol, b.protocol),
  )) {
    lines.push(
      "",
      `export interface ${declaration.protocol} {`,
      `  ${declaration.method}(): void;`,
      "}",
    );
  }
  if (plan.usesJsonValue) lines.push("", `export type JSONValue = ${JSON_VALUE_ALIAS_RHS};`);
  for (const item of [...plan.types].sort((a, b) => compare(a.name, b.name))) {
    lines.push("");
    if (item.kind === "enum") {
      lines.push(...docComment(item.description));
      lines.push(`export enum ${item.name} {`);
      item.members.forEach(([name, value], index) => {
        lines.push(...docComment(item.memberDescriptions[index], "  "));
        lines.push(`  ${name} = ${JSON.stringify(value)},`);
      });
      lines.push("}");
    } else if (item.kind === "alias") {
      lines.push(...docComment(item.description));
      lines.push(`export type ${item.name} = ${item.annotation};`);
    } else {
      lines.push(...renderObject(item));
    }
  }
  for (const declaration of [...plan.declarations].sort((a, b) =>
    compare(a.function, b.function),
  )) {
    lines.push(
      "",
      ...docComment(
        declaration.description
          ? `Declares a ${declaration.kind} Condition.\n\n${declaration.description}`
          : undefined,
      ),
      `export function ${declaration.function}(...fields: ${declaration.protocol}[]): Declaration {`,
      "  void fields;",
      "  return new Declaration();",
      "}",
    );
  }
  lines.push("");
  return lines.join("\n");
}

function indexSource(model: Model): string {
  return `${sourceHeader(model)}\n\nexport * from "./bindings.js";\n`;
}

export const FORMAT_OPTIONS: prettier.Options = JSON.parse(readTemplate("prettier.json"));

export async function formatSource(source: string, filepath: string): Promise<string> {
  return prettier.format(source, { ...FORMAT_OPTIONS, filepath });
}

export async function renderSources(
  plan: EmissionPlan,
  model: Model,
): Promise<Record<string, string>> {
  if (
    model["metadata"].semanticSha256 !== plan.modelDigest ||
    model["rootExtension"].id !== plan.target.rootExtension
  ) {
    fail("model", "RCT1003", "model", "emission plan does not match supplied model");
  }
  const examples = await renderExamples(plan, model, FORMAT_OPTIONS);
  currentExamples = examples;
  let bindings: string;
  try {
    bindings = bindingsSource(plan, model);
  } finally {
    currentExamples = new Map();
  }
  const sources = {
    "src/index.ts": indexSource(model),
    "src/bindings.ts": bindings,
  };
  const formatted: Record<string, string> = {};
  for (const [path, source] of Object.entries(sources)) {
    try {
      formatted[path] = await formatSource(source, path);
    } catch (error) {
      fail(
        "toolchain",
        "RCT4002",
        plan.target.packageKey,
        `Prettier could not format ${path}: ${(error as Error).message.split("\n")[0]}`,
      );
    }
  }
  const readme = readmeSource(plan, model, examples);
  if (readme !== null) formatted["README.md"] = readme;
  return formatted;
}

function markdown(
  text: string,
  plan: EmissionPlan,
  examples: ReadonlyMap<string, RenderedExample>,
): string[] {
  const lines: string[] = [];
  for (const segment of splitDocumentation(text.trim())) {
    if (lines.length > 0) lines.push("");
    if (segment.condition === undefined) {
      lines.push(segment.text!);
      continue;
    }
    const example = examples.get(segment.source!);
    if (example !== undefined) {
      lines.push(
        "```ts",
        ...example.imports.map(([pkg, names]) => `import { ${names.join(", ")} } from ${JSON.stringify(pkg)};`),
        "",
        example.code,
        "```",
        "",
        "In a Profile, this declares:",
        "",
      );
    }
    lines.push("```yaml", dedent(segment.source!).trim(), "```");
  }
  return lines;
}

// readmeSource writes the package README from the extension's documentation.
// Undocumented extensions get none, so their packages keep the same files.
function readmeSource(
  plan: EmissionPlan,
  model: Model,
  examples: ReadonlyMap<string, RenderedExample>,
): string | null {
  if (!plan.packageDescription) return null;
  const lines = [
    `<!-- ${header(model)} -->`,
    "",
    `# ${plan.target.packageName}`,
    "",
    ...markdown(plan.packageDescription, plan, examples),
    "",
    "```sh",
    `npm install ${plan.target.packageName}`,
    "```",
    "",
    `Runtime Conditions extension: ${plan.target.rootExtension}`,
  ];
  for (const declaration of [...plan.declarations].sort((a, b) => compare(a.function, b.function))) {
    lines.push("", `## ${declaration.function}`, "");
    lines.push(`\`${declaration.function}\` declares a \`${declaration.kind}\` Condition.`);
    if (declaration.description) lines.push("", ...markdown(declaration.description, plan, examples));
  }
  // Fields this package adds to Conditions; fields sharing one description share a section.
  const groups = new Map<string, ConditionFieldDoc[]>();
  for (const field of conditionFieldDocs(plan, model)) {
    groups.set(field.description, [...(groups.get(field.description) ?? []), field]);
  }
  for (const [description, fields] of groups) {
    lines.push("", `## ${fields.map((field) => field.name).join(", ")}`, "");
    for (const field of fields) {
      const scope = field.interfaceType ? ` with interface type \`${field.interfaceType}\`` : "";
      lines.push(`\`${field.name}\` sets the \`${field.path}\` field of \`${field.kind}\` Conditions${scope}.`);
    }
    lines.push("", ...markdown(description, plan, examples));
  }
  lines.push(
    "",
    "Every class, field and enum member is documented in the package's type declarations.",
    "",
  );
  return lines.join("\n");
}

export async function renderPackage(
  plan: EmissionPlan,
  model: Model,
): Promise<Record<string, string>> {
  return { ...(await renderSources(plan, model)), ...renderResources(plan, model) };
}

export function writeFiles(
  files: Record<string, string>,
  plan: EmissionPlan,
  outputDirectory: string,
): string[] {
  let occupied = false;
  try {
    occupied =
      existsSync(outputDirectory) &&
      (lstatSync(outputDirectory).isSymbolicLink() ||
        !lstatSync(outputDirectory).isDirectory() ||
        readdirSync(outputDirectory).length > 0);
  } catch {
    fail("output", "RCT3002", plan.target.packageKey, "cannot inspect output directory");
  }
  if (occupied) {
    fail(
      "output",
      "RCT3001",
      plan.target.packageKey,
      "output directory must be new or empty and not a symbolic link",
    );
  }
  try {
    mkdirSync(outputDirectory, { recursive: true });
    for (const relative of Object.keys(files).sort(compare)) {
      const destination = join(outputDirectory, relative);
      mkdirSync(dirname(destination), { recursive: true });
      writeFileSync(destination, files[relative]!, { encoding: "utf-8", flag: "wx" });
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") {
      fail(
        "output",
        "RCT3001",
        plan.target.packageKey,
        "output directory must be new or empty and not a symbolic link",
      );
    }
    fail("output", "RCT3002", plan.target.packageKey, "cannot write generated package files");
  }
  return Object.keys(files).sort(compare);
}

export async function emitPackage(
  plan: EmissionPlan,
  model: Model,
  outputDirectory: string,
): Promise<string[]> {
  return writeFiles(await renderPackage(plan, model), plan, outputDirectory);
}

import * as prettier from "prettier";
import { parse } from "yaml";

import type { Model } from "./model.js";
import type { EmissionPlan, TypeExpr, TypePlan } from "./plan.js";

// Descriptions may contain example Conditions in ```condition fences, a
// first-party documentation convention checked by the normalizer. The
// TypeScript emitter renders each example as the declaration call that produces
// it, in JSDoc and in the package README. An example that this package cannot
// express on its own is shown as Profile YAML instead.

export const CONDITION_FENCE = "condition";

export interface DocSegment {
  readonly text?: string;
  readonly source?: string;
  readonly condition?: Record<string, unknown>;
}

export interface RenderedExample {
  readonly code: string;
  readonly imports: readonly string[];
}

export function dedent(text: string): string {
  const lines = text.split("\n");
  const indents = lines
    .filter((line) => line.trim() !== "")
    .map((line) => line.length - line.trimStart().length);
  const common = indents.length > 0 ? Math.min(...indents) : 0;
  return lines.map((line) => line.slice(Math.min(common, line.length - line.trimStart().length))).join("\n");
}

/** Splits a description into prose and example Conditions. */
export function splitDocumentation(text: string): DocSegment[] {
  const segments: DocSegment[] = [];
  let prose: string[] = [];
  let example: string[] | null = null;
  const flush = (): void => {
    const joined = prose.join("\n").trim();
    if (joined !== "") segments.push({ text: joined });
    prose = [];
  };
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (example === null && trimmed === "```" + CONDITION_FENCE) {
      flush();
      example = [];
    } else if (example !== null && trimmed === "```") {
      const source = example.join("\n");
      let condition: unknown;
      try {
        condition = parse(dedent(source));
      } catch {
        condition = undefined;
      }
      if (condition !== null && typeof condition === "object" && !Array.isArray(condition)) {
        segments.push({ source, condition: condition as Record<string, unknown> });
      } else {
        prose.push("```yaml", source, "```");
      }
      example = null;
    } else if (example !== null) {
      example.push(line);
    } else {
      prose.push(line);
    }
  }
  if (example !== null) prose.push("```" + CONDITION_FENCE, ...example);
  flush();
  return segments;
}

class ExampleError extends Error {}

function identifier(name: string): string {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name) ? name : JSON.stringify(name);
}

class Renderer {
  readonly imports = new Set<string>();
  private readonly types: ReadonlyMap<string, TypePlan>;

  constructor(private readonly plan: EmissionPlan) {
    this.types = new Map(plan.types.map((item) => [item.name, item]));
  }

  value(expression: TypeExpr, value: unknown): string {
    switch (expression.kind) {
      case "string":
        if (typeof value === "string") return JSON.stringify(value);
        break;
      case "boolean":
        if (typeof value === "boolean") return String(value);
        break;
      case "integer":
        if (typeof value === "number" && Number.isInteger(value)) return String(value);
        break;
      case "number":
        if (typeof value === "number") return String(value);
        break;
      case "null":
        if (value === null) return "null";
        break;
      case "any":
        return JSON.stringify(value);
      case "array":
        if (Array.isArray(value)) {
          return `[${value.map((item) => this.value(expression.arguments[0]!, item)).join(", ")}]`;
        }
        break;
      case "map":
        if (value !== null && typeof value === "object" && !Array.isArray(value)) {
          const entries = Object.keys(value)
            .sort()
            .map(
              (key) =>
                `${identifier(key)}: ${this.value(expression.arguments[0]!, (value as Record<string, unknown>)[key])}`,
            );
          return `{ ${entries.join(", ")} }`;
        }
        break;
      case "union":
        for (const variant of expression.arguments) {
          try {
            return this.value(variant, value);
          } catch (error) {
            if (!(error instanceof ExampleError)) throw error;
          }
        }
        break;
      default:
        return this.named(this.plan.names.get(expression.symbolKey)!, value);
    }
    throw new ExampleError(`value ${JSON.stringify(value)} does not fit ${expression.kind}`);
  }

  named(name: string, value: unknown): string {
    const type = this.types.get(name);
    if (type === undefined) throw new ExampleError(`unknown type ${name}`);
    if (type.kind === "enum") {
      const member = type.members.find(([, memberValue]) => memberValue === value);
      if (member === undefined) throw new ExampleError(`${JSON.stringify(value)} is not a ${name}`);
      this.imports.add(name);
      return `${name}.${member[0]}`;
    }
    if (type.kind === "alias") return this.value(type.expression!, value);
    return this.object(type, value);
  }

  object(type: TypePlan, value: unknown): string {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      throw new ExampleError(`${type.name} expects an object`);
    }
    const object = value as Record<string, unknown>;
    const known = new Set(["type", ...type.fields.map((field) => field.sourceName)]);
    const unknown = Object.keys(object).filter((key) => !known.has(key));
    if (unknown.length > 0) throw new ExampleError(`${type.name} has no field ${unknown[0]}`);
    const entries = type.fields
      .filter((field) => field.sourceName in object)
      .map((field) => `${field.name}: ${this.value(field.typeExpr, object[field.sourceName])}`);
    this.imports.add(type.name);
    return entries.length > 0 ? `new ${type.name}({ ${entries.join(", ")} })` : `new ${type.name}()`;
  }

  /** The declaration-field type for an interface or condition field root. */
  root(model: Model, kind: string, interfaceType: string, sourceName: string): TypePlan | undefined {
    const schemas = new Map<string, any>((model["schemas"] ?? []).map((item: any) => [item.coordinate, item]));
    const candidates = this.plan.types.filter((item) => {
      if (!item.isDeclarationField || item.sourceName !== sourceName) return false;
      const schema = schemas.get(item.schemaCoordinate);
      return (
        schema !== undefined &&
        (schema.kind ?? schema.id) === kind &&
        (schema.interfaceType === undefined || schema.interfaceType === interfaceType)
      );
    });
    // Prefer a schema scoped to the interface type over a kind-wide one.
    candidates.sort(
      (a, b) =>
        Number(schemas.get(b.schemaCoordinate).interfaceType === interfaceType) -
        Number(schemas.get(a.schemaCoordinate).interfaceType === interfaceType),
    );
    return candidates[0];
  }

  condition(model: Model, condition: Record<string, unknown>): string {
    const kind = condition["kind"];
    const declaration = this.plan.declarations.find((item) => item.kind === kind);
    if (declaration === undefined) throw new ExampleError(`kind ${String(kind)} is not declared here`);
    const fieldArguments: string[] = [];
    const iface = condition["interface"] as Record<string, unknown> | undefined;
    const interfaceType = typeof iface?.["type"] === "string" ? (iface["type"] as string) : "";
    if (iface !== undefined) {
      const type = this.root(model, kind as string, interfaceType, interfaceType);
      if (type === undefined) throw new ExampleError(`interface ${interfaceType} is not defined here`);
      fieldArguments.push(this.object(type, iface));
    }
    for (const key of Object.keys(condition).sort()) {
      if (key === "kind" || key === "interface" || key === "name") continue;
      const type = this.root(model, kind as string, interfaceType, key);
      if (type === undefined) throw new ExampleError(`condition field ${key} is not defined here`);
      const single = type.fields.length === 1 && type.fields[0]!.sourceName === "value";
      fieldArguments.push(
        single && (typeof condition[key] !== "object" || condition[key] === null)
          ? this.object(type, { value: condition[key] })
          : this.object(type, condition[key]),
      );
    }
    this.imports.add(declaration.function);
    return `${declaration.function}(${fieldArguments.join(", ")});`;
  }
}

/** Renders every example Condition in the model's descriptions, keyed by source. */
export async function renderExamples(
  plan: EmissionPlan,
  model: Model,
  formatOptions: prettier.Options,
): Promise<Map<string, RenderedExample>> {
  const texts: string[] = [];
  const root = model["rootExtension"].id;
  for (const extension of model["extensions"] ?? []) {
    if (extension.id === root && extension.description) texts.push(extension.description);
  }
  for (const item of model["vocabulary"]?.["ownedDeclarations"] ?? []) {
    if (item.description) texts.push(item.description);
  }
  const rendered = new Map<string, RenderedExample>();
  for (const text of texts) {
    for (const segment of splitDocumentation(text)) {
      if (segment.condition === undefined || rendered.has(segment.source!)) continue;
      const renderer = new Renderer(plan);
      let code: string;
      try {
        code = renderer.condition(model, segment.condition);
      } catch (error) {
        if (error instanceof ExampleError) continue;
        throw error;
      }
      const formatted = await prettier.format(code, { ...formatOptions, parser: "typescript" });
      rendered.set(segment.source!, {
        code: formatted.trim(),
        imports: [...renderer.imports].sort(),
      });
    }
  }
  return rendered;
}

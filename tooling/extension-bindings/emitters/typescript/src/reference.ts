// The README's "Conditions" reference: one collapsible entry per interface type
// and per condition field the package defines, listing every field a user can
// set, its type, whether it is required, what it means and its allowed values.
// It uses <details>, which npm and GitHub render as a line that opens on click.

import { rootType, splitDocumentation, summary } from "./examples.js";
import type { Model } from "./model.js";
import type { EmissionPlan, TypeExpr, TypePlan } from "./plan.js";

interface Row {
  readonly path: string;
  readonly type: string;
  readonly required: boolean;
  readonly description: string;
}

/** Prose of a description on one line, without examples, safe inside a table cell. */
function cell(text: string): string {
  return splitDocumentation(text)
    .filter((segment) => segment.condition === undefined)
    .map((segment) => segment.text!)
    .join(" ")
    .replace(/\s+/g, " ")
    .replace(/\|/g, "\\|")
    .trim();
}

function rows(plan: EmissionPlan, type: TypePlan): Row[] {
  const types = new Map(plan.types.map((item) => [item.name, item]));
  const result: Row[] = [];
  const seen = new Set<string>();
  const named = (expression: TypeExpr): TypePlan | undefined =>
    expression.symbolKey ? types.get(plan.names.get(expression.symbolKey) ?? "") : undefined;
  // The enum a field's values come from, directly or through an alias or a list.
  const enumOf = (expression: TypeExpr, through = new Set<string>()): TypePlan | undefined => {
    if (expression.kind === "array") return enumOf(expression.arguments[0]!, through);
    const target = named(expression);
    if (target === undefined || through.has(target.name)) return undefined;
    if (target.kind === "enum") return target;
    if (target.kind === "alias" && target.expression) {
      return enumOf(target.expression, new Set([...through, target.name]));
    }
    return undefined;
  };
  // The objects nested under a field, with the path suffix that reaches them.
  const nested = (expression: TypeExpr, suffix: string, through = new Set<string>()): [TypePlan, string][] => {
    if (expression.kind === "array") return nested(expression.arguments[0]!, `${suffix}[]`, through);
    if (expression.kind === "map") return nested(expression.arguments[0]!, `${suffix}.*`, through);
    if (expression.kind === "union") {
      return expression.arguments.flatMap((item) => nested(item, suffix, through));
    }
    const target = named(expression);
    if (target === undefined || through.has(target.name)) return [];
    if (target.kind === "object") return [[target, suffix]];
    if (target.kind === "alias" && target.expression) {
      return nested(target.expression, suffix, new Set([...through, target.name]));
    }
    return [];
  };
  const visit = (current: TypePlan, prefix: string, stack: Set<string>): void => {
    for (const field of current.fields) {
      const path = `${prefix}${field.name}`;
      if (seen.has(path)) continue;
      seen.add(path);
      let description = cell(field.description);
      const values = enumOf(field.typeExpr);
      if (values !== undefined) {
        const listed = values.members.map(([, value], index) => {
          const meaning = cell(values.memberDescriptions[index] ?? "");
          return meaning ? `\`${value}\`: ${meaning}` : `\`${value}\``;
        });
        description = [description, "Values:", ...listed].filter(Boolean).join("<br>");
      }
      result.push({ path, type: field.annotation, required: field.required, description });
      for (const [child, suffix] of nested(field.typeExpr, "")) {
        if (stack.has(child.name)) continue;
        visit(child, `${path}${suffix}.`, new Set([...stack, child.name]));
      }
    }
  };
  visit(type, "", new Set([type.name]));
  return result;
}

/** The first sentence of a description's summary, for an entry's one-line heading. */
function headline(text: string): string {
  const first = summary(text);
  const end = first.search(/\.(\s|$)/);
  return end === -1 ? first : first.slice(0, end + 1);
}

function article(word: string): string {
  return /^[aeiou]/i.test(word) ? "an" : "a";
}

function entry(plan: EmissionPlan, heading: string, sentence: string, type: TypePlan): string[] {
  const lines = ["<details>", `<summary>${heading}</summary>`, "", sentence, ""];
  const table = rows(plan, type);
  if (table.length === 0) {
    lines.push("It has no fields to set.", "");
  } else {
    lines.push("| Field | Type | Required | Description |", "| --- | --- | --- | --- |");
    for (const row of table) {
      const type = `\`${row.type.replace(/\|/g, "\\|")}\``;
      lines.push(`| \`${row.path}\` | ${type} | ${row.required ? "yes" : "no"} | ${row.description} |`);
    }
    lines.push("");
  }
  lines.push("</details>");
  return lines;
}

/** The README's collapsible reference of every interface and condition field this package defines. */
export function conditionsReference(plan: EmissionPlan, model: Model): string[] {
  const root = model["rootExtension"].id;
  const vocabulary = model["vocabulary"] ?? {};
  const entries: string[][] = [];
  const order = (items: any[]): any[] =>
    [...items].sort((a, b) =>
      `${a.kind}\0${a.type ?? a.interfaceType ?? ""}` < `${b.kind}\0${b.type ?? b.interfaceType ?? ""}` ? -1 : 1,
    );
  for (const item of order((vocabulary.interfaces ?? []).filter((item: any) => item.owner === root))) {
    const type = rootType(plan, model, item.kind, item.type, item.type);
    if (type === undefined) continue;
    const about = headline(item.description ?? "");
    const heading = `<b>${item.kind}</b> · <code>${item.type}</code>${about ? `: ${about}` : ""}`;
    const sentence = `\`${type.name}\` is the \`interface\` of ${article(item.kind)} \`${item.kind}\` Condition.`;
    entries.push(entry(plan, heading, sentence, type));
  }
  for (const item of order((vocabulary.conditionFields ?? []).filter((item: any) => item.owner === root))) {
    const segments = item.segments ?? [];
    const sourceName = segments.length > 0 ? segments[segments.length - 1].name : item.path;
    const type = rootType(plan, model, item.kind, item.interfaceType ?? "", sourceName);
    if (type === undefined) continue;
    const scope = item.interfaceType ? ` · <code>${item.interfaceType}</code>` : "";
    const about = headline(item.description ?? "");
    const heading = `<code>${item.path}</code> on <b>${item.kind}</b>${scope}${about ? `: ${about}` : ""}`;
    const sentence = `\`${type.name}\` sets the \`${item.path}\` field of ${article(item.kind)} \`${item.kind}\` Condition.`;
    entries.push(entry(plan, heading, sentence, type));
  }
  if (entries.length === 0) return [];
  const lines = [
    "## Conditions",
    "",
    "Every Condition part this package defines, with the fields you can set. Select a line to open it.",
  ];
  for (const item of entries) lines.push("", ...item);
  return lines;
}

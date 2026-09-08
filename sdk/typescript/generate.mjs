import fs from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";
import prettier from "prettier";

const source = new URL("./openapi.json", import.meta.url);
const target = new URL("./src/schema.ts", import.meta.url);
const ast = await openapiTS(source, {
  transform(schema) {
    if (schema.format === "binary")
      return ts.factory.createTypeReferenceNode("Binary");
  },
});
const content = await prettier.format(
  astToString(ast) +
    "\nexport type Binary = Blob | ReadableStream<Uint8Array>;\n",
  { parser: "typescript" },
);
if (process.argv.includes("--check")) {
  if ((await fs.readFile(target, "utf8")) !== content) {
    throw new Error(
      "Generated Service types changed. Run make sdk-typescript-generate.",
    );
  }
} else {
  await fs.writeFile(target, content);
  await fs.writeFile(
    source,
    await prettier.format(await fs.readFile(source, "utf8"), {
      parser: "json",
    }),
  );
}

import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd();

function read(relativePath) {
  return readFileSync(path.join(root, relativePath), "utf8");
}

test("users feature files are generated", () => {
  const requiredFiles = [
    "src/users/users.module.ts",
    "src/users/users.controller.ts",
    "src/users/users.service.ts",
    "src/users/dto/create-users.dto.ts",
  ];

  for (const filePath of requiredFiles) {
    assert.equal(existsSync(path.join(root, filePath)), true, `expected ${filePath} to exist`);
  }
});

test("app module wires the users module", () => {
  const content = read("src/app.module.ts");
  assert.match(content, /import\s+\{\s*UsersModule\s*\}\s+from\s+"\.\/users\/users\.module";/);
  assert.match(content, /imports:\s*\[\s*UsersModule\s*\]/);
});

test("controller exposes GET users and delegates to service", () => {
  const content = read("src/users/users.controller.ts");
  assert.match(content, /@Controller\("users"\)/);
  assert.match(content, /@Get\(\)/);
  assert.match(content, /return this\.usersService\.list\(\);/);
});

test("service returns placeholder users list", () => {
  const content = read("src/users/users.service.ts");
  assert.match(content, /list\(\)\s*\{/);
  assert.match(content, /Sample Users/);
});

test("dto shape exists", () => {
  const content = read("src/users/dto/create-users.dto.ts");
  assert.match(content, /export class CreateUsersDto/);
  assert.match(content, /name!: string;/);
});
import { expect, it } from "vitest";
import { GET } from "../../src/app/api/health/route";

it("returns health without model credentials", async () => {
  const response = GET();

  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ status: "ok" });
});

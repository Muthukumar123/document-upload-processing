import { app, HttpRequest, HttpResponseInit } from "@azure/functions";

export async function mockValidation(request: HttpRequest): Promise<HttpResponseInit> {
  const body = await request.json() as any;
  await new Promise(r => setTimeout(r, 25));
  const values = Array.isArray(body?.values) ? body.values : [];
  const valid = values.length === 10 && values[0] !== "INVALID" && body.compareMatch !== false;
  return { status: 200, jsonBody: { valid, reason: valid ? undefined : "Reference comparison or validation rule failed" } };
}
app.http("mockValidation", { methods: ["POST"], authLevel: "anonymous", route: "mock/validate", handler: mockValidation });

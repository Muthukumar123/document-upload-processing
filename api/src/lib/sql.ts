import sql from "mssql";
import { config } from "../config.js";

let poolPromise: Promise<sql.ConnectionPool> | undefined;
export function db(): Promise<sql.ConnectionPool> {
  poolPromise ??= new sql.ConnectionPool(config.SQL_CONNECTION_STRING).connect();
  return poolPromise;
}

export async function tx<T>(work: (t: sql.Transaction) => Promise<T>): Promise<T> {
  const pool = await db();
  const t = new sql.Transaction(pool);
  await t.begin(sql.ISOLATION_LEVEL.READ_COMMITTED);
  try {
    const result = await work(t);
    await t.commit();
    return result;
  } catch (e) {
    await t.rollback();
    throw e;
  }
}

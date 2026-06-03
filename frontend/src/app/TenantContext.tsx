/**
 * TenantContext — 전역 가입자(tenant) 선택 컨텍스트.
 * 모든 화면이 선택된 tenant 기준으로 데이터를 조회한다.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Tenant } from "../types";

interface TenantCtx {
  tenants: Tenant[];
  current: Tenant | null;
  setCurrent: (t: Tenant) => void;
  loading: boolean;
}

const Ctx = createContext<TenantCtx>({
  tenants: [],
  current: null,
  setCurrent: () => {},
  loading: true,
});

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [current, setCurrentState] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .tenants()
      .then((list) => {
        setTenants(list);
        const saved = localStorage.getItem("aa-tenant");
        const found = list.find((t) => t.tenant_id === saved);
        setCurrentState(found ?? list[0] ?? null);
      })
      .catch(() => setTenants([]))
      .finally(() => setLoading(false));
  }, []);

  const setCurrent = (t: Tenant) => {
    setCurrentState(t);
    localStorage.setItem("aa-tenant", t.tenant_id);
  };

  return (
    <Ctx.Provider value={{ tenants, current, setCurrent, loading }}>
      {children}
    </Ctx.Provider>
  );
}

export const useTenant = () => useContext(Ctx);

import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { authApi } from '../services/api';
import type { Me } from '../types';

interface MeState {
  me: Me | null;
  reload: () => Promise<void>;
  isAdmin: boolean;
  canManage: boolean;
}

const MeContext = createContext<MeState>({ me: null, reload: async () => {}, isAdmin: false, canManage: false });

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const reload = useCallback(async () => {
    try {
      const res = await authApi.me();
      setMe(res.data);
    } catch {
      setMe(null);
    }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  return (
    <MeContext.Provider value={{
      me,
      reload,
      isAdmin: me?.role === 'admin',
      canManage: me?.role === 'admin' || me?.role === 'operator',
    }}>
      {children}
    </MeContext.Provider>
  );
}

export const useMe = () => useContext(MeContext);

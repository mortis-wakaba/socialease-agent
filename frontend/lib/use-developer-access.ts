"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  frontendAuthMode,
  isAuthenticatedForFrontend,
  subscribeAuthState
} from "@/lib/auth";

export type DeveloperAccessState = {
  ready: boolean;
  authenticated: boolean;
  allowed: boolean;
};

export function useDeveloperAccess(): DeveloperAccessState {
  const [state, setState] = useState<DeveloperAccessState>({
    ready: false,
    authenticated: false,
    allowed: false
  });

  useEffect(() => {
    let active = true;
    let requestVersion = 0;

    async function refresh() {
      const version = ++requestVersion;
      const authenticated = isAuthenticatedForFrontend();
      if (!authenticated) {
        if (active && version === requestVersion) {
          setState({ ready: true, authenticated: false, allowed: false });
        }
        return;
      }

      if (frontendAuthMode() !== "production") {
        if (active && version === requestVersion) {
          setState({ ready: true, authenticated: true, allowed: true });
        }
        return;
      }

      try {
        const me = await api.authMe();
        if (active && version === requestVersion) {
          setState({
            ready: true,
            authenticated: true,
            allowed: me.developer_access
          });
        }
      } catch {
        if (active && version === requestVersion) {
          setState({ ready: true, authenticated: true, allowed: false });
        }
      }
    }

    void refresh();
    const unsubscribe = subscribeAuthState(() => {
      void refresh();
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  return state;
}

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren
} from "react";
import { apiFetch } from "./api";

const ACCESS_TOKEN_KEY = "nc_access_token";
const SETUP_TOKEN_KEY = "nc_setup_token";

export type AuthBundle = {
  status: "anonymous" | "profile_incomplete" | "authorized";
  access_token?: string | null;
  setup_token?: string | null;
  user?: Record<string, unknown>;
  tenant?: Record<string, unknown>;
  workspace?: Record<string, unknown>;
  onboarding?: Record<string, unknown>;
};

export type AuthState = {
  ready: boolean;
  status: "anonymous" | "profile_incomplete" | "authenticated";
  accessToken: string | null;
  setupToken: string | null;
  profile: AuthBundle | null;
};

type AuthContextValue = AuthState & {
  verifyTelegram: (payload: Record<string, unknown>) => Promise<AuthBundle>;
  completeProfile: (email: string, company: string) => Promise<AuthBundle>;
  loginWithEmail: (email: string, password: string) => Promise<AuthBundle>;
  registerWithEmail: (email: string, password: string, firstName: string, company: string) => Promise<AuthBundle>;
  refresh: () => Promise<AuthBundle | null>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function readAccessToken(): string | null {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

function writeAccessToken(value: string | null): void {
  if (value) {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, value);
  } else {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  }
}

function readSetupToken(): string | null {
  return window.sessionStorage.getItem(SETUP_TOKEN_KEY);
}

function writeSetupToken(value: string | null): void {
  if (value) {
    window.sessionStorage.setItem(SETUP_TOKEN_KEY, value);
  } else {
    window.sessionStorage.removeItem(SETUP_TOKEN_KEY);
  }
}

function normalizeBundle(bundle: AuthBundle): AuthBundle {
  if (bundle.status === "authorized" && bundle.access_token) {
    writeAccessToken(bundle.access_token);
    writeSetupToken(null);
  } else if (bundle.status === "profile_incomplete" && bundle.setup_token) {
    writeAccessToken(null);
    writeSetupToken(bundle.setup_token);
  } else if (bundle.status === "anonymous") {
    writeAccessToken(null);
    writeSetupToken(null);
  }
  return bundle;
}

function toState(bundle: AuthBundle | null): AuthState {
  if (!bundle) {
    return {
      ready: true,
      status: "anonymous",
      accessToken: null,
      setupToken: null,
      profile: null
    };
  }
  if (bundle.status === "profile_incomplete") {
    return {
      ready: true,
      status: "profile_incomplete",
      accessToken: null,
      setupToken: bundle.setup_token || readSetupToken(),
      profile: bundle
    };
  }
  if (bundle.status === "authorized") {
    return {
      ready: true,
      status: "authenticated",
      accessToken: bundle.access_token || readAccessToken(),
      setupToken: null,
      profile: bundle
    };
  }
  return {
    ready: true,
    status: "anonymous",
    accessToken: null,
    setupToken: null,
    profile: null
  };
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [state, setState] = useState<AuthState>({
    ready: false,
    status: "anonymous",
    accessToken: readAccessToken(),
    setupToken: readSetupToken(),
    profile: null
  });

  const applyBundle = useCallback((bundle: AuthBundle | null) => {
    const normalized = bundle ? normalizeBundle(bundle) : null;
    setState(toState(normalized));
    return normalized;
  }, []);

  const refresh = useCallback(async () => {
    try {
      const bundle = await apiFetch<AuthBundle>("/auth/refresh", { method: "POST" });
      return applyBundle(bundle);
    } catch {
      applyBundle(null);
      return null;
    }
  }, [applyBundle]);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      if (!readAccessToken()) {
        const bundle = await refresh();
        if (cancelled) {
          return;
        }
        if (bundle) {
          return;
        }
      }
      const accessToken = readAccessToken();
      if (!accessToken) {
        if (!cancelled) {
          setState(toState(null));
        }
        return;
      }
      try {
        const me = await apiFetch<AuthBundle>("/auth/me", { accessToken });
        if (!cancelled) {
          applyBundle({ ...me, status: "authorized", access_token: accessToken });
        }
      } catch {
        if (!cancelled) {
          await refresh();
        }
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [applyBundle, refresh]);

  const verifyTelegram = useCallback(
    async (payload: Record<string, unknown>) => {
      const bundle = await apiFetch<AuthBundle>("/auth/telegram/verify", {
        method: "POST",
        json: payload
      });
      return applyBundle(bundle) as AuthBundle;
    },
    [applyBundle]
  );

  const completeProfile = useCallback(
    async (email: string, company: string) => {
      const setupToken = readSetupToken();
      if (!setupToken) {
        throw new Error("setup_token_missing");
      }
      const bundle = await apiFetch<AuthBundle>("/auth/complete-profile", {
        method: "POST",
        json: { setup_token: setupToken, email, company }
      });
      return applyBundle(bundle) as AuthBundle;
    },
    [applyBundle]
  );

  const logout = useCallback(async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } finally {
      applyBundle(null);
    }
  }, [applyBundle]);

  const loginWithEmail = useCallback(
    async (email: string, password: string) => {
      const bundle = await apiFetch<AuthBundle>("/auth/login", {
        method: "POST",
        json: { email, password }
      });
      return applyBundle(bundle) as AuthBundle;
    },
    [applyBundle]
  );

  const registerWithEmail = useCallback(
    async (email: string, password: string, firstName: string, company: string) => {
      const bundle = await apiFetch<AuthBundle>("/auth/register", {
        method: "POST",
        json: { email, password, first_name: firstName, company }
      });
      return applyBundle(bundle) as AuthBundle;
    },
    [applyBundle]
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      verifyTelegram,
      completeProfile,
      loginWithEmail,
      registerWithEmail,
      refresh,
      logout
    }),
    [state, verifyTelegram, completeProfile, loginWithEmail, registerWithEmail, refresh, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}

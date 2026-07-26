const ONBOARDING_KEY = "socialease.onboarding";

export type OnboardingState = {
  completed: boolean;
  primaryGoal: string;
  preferredScenario: string;
  anxietyLevel: number;
  savePreferences: boolean;
  boundaryAcknowledged: boolean;
  completedAt: string;
};

export function getOnboardingState(): OnboardingState | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.localStorage.getItem(ONBOARDING_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as OnboardingState;
  } catch {
    return null;
  }
}

export function saveOnboardingState(
  state: Omit<OnboardingState, "completed" | "completedAt">
) {
  if (typeof window === "undefined") {
    return;
  }
  const payload: OnboardingState = {
    ...state,
    completed: true,
    completedAt: new Date().toISOString()
  };
  window.localStorage.setItem(ONBOARDING_KEY, JSON.stringify(payload));
  window.dispatchEvent(new Event("socialease:onboarding"));
}

export function clearOnboardingState() {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(ONBOARDING_KEY);
  window.dispatchEvent(new Event("socialease:onboarding"));
}

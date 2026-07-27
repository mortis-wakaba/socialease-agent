"use client";

import { FormEvent, useEffect, useState } from "react";
import type React from "react";
import Link from "next/link";
import { ConsentRequiredError, api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { saveOnboardingState } from "@/lib/onboarding";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import { DirectConsentCard } from "@/components/direct-consent-card";
import type {
  ConsentRequiredDetail,
  OnboardingPrimaryGoal,
  PracticePreferences,
  UserOnboardingProfile
} from "@/lib/types";
import {
  Badge,
  Button,
  ErrorBox,
  FormHint,
  PageHeader,
  Panel,
  Select,
  TextArea
} from "@/components/ui";

const goals: Array<{ value: OnboardingPrimaryGoal; label: string }> = [
  { value: "clearer_classroom_expression", label: "课堂表达更清楚" },
  {
    value: "steadier_group_or_dorm_communication",
    label: "宿舍或小组沟通更稳"
  },
  { value: "boundary_and_refusal_practice", label: "练习拒绝和边界表达" },
  { value: "interview_self_intro_confidence", label: "面试或自我介绍更自然" }
];

export default function OnboardingPage() {
  const auth = useRequireAuth();
  const [primaryGoal, setPrimaryGoal] = useState<OnboardingPrimaryGoal>(
    goals[0].value
  );
  const [preferredScenario, setPreferredScenario] = useState("");
  const [anxietyLevel, setAnxietyLevel] = useState(5);
  const [savePreferences, setSavePreferences] = useState(true);
  const [boundaryAcknowledged, setBoundaryAcknowledged] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingConsent, setPendingConsent] = useState<PendingOnboardingConsent | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    void loadOnboardingProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, auth.userId]);

  async function loadOnboardingProfile() {
    try {
      const result = await api.getOnboardingProfile(currentUserId());
      const profile = result.onboarding_profile;
      if (profile.primary_goal) {
        setPrimaryGoal(profile.primary_goal);
      }
      if (profile.preferred_scenario) {
        setPreferredScenario(profile.preferred_scenario);
      }
      if (profile.current_anxiety_level) {
        setAnxietyLevel(profile.current_anxiety_level);
      }
      setBoundaryAcknowledged(profile.boundary_acknowledged);
    } catch {
      // Local onboarding can still work when the backend is unavailable.
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!boundaryAcknowledged) {
      setError("继续前请先确认你了解非医疗边界和危机边界。");
      return;
    }
    const state = {
      primaryGoal,
      preferredScenario,
      anxietyLevel,
      savePreferences,
      boundaryAcknowledged
    };
    try {
      await saveBackendOnboarding(state);
      saveOnboardingState(state);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法保存开始前设置");
      return;
    }
    if (!savePreferences) {
      setStatus("已保存开始前设置。长期练习偏好没有保存。");
      return;
    }
    await savePreferencesWithConsent(state);
  }

  async function savePreferencesWithConsent(
    state: OnboardingDraft,
    protocolId?: string
  ) {
    setLoading(true);
    setError(null);
    setStatus(null);
    const preferences: PracticePreferences = {
      preferred_roleplay_difficulty: difficultyFromAnxiety(state.anxietyLevel),
      preferred_feedback_style: "gentle_specific",
      preferred_practice_scenarios: [state.preferredScenario]
    };
    try {
      await api.updateMemoryPreferences(currentUserId(), preferences, { protocolId });
      setPendingConsent(null);
      setStatus("已完成引导，并在明确同意后保存了低敏练习偏好。");
    } catch (err) {
      if (err instanceof ConsentRequiredError) {
        setPendingConsent({ detail: err.detail, state });
        setStatus("保存长期偏好前需要明确同意。");
      } else {
        setError(err instanceof Error ? err.message : "无法保存引导偏好");
      }
    } finally {
      setLoading(false);
    }
  }

  async function saveBackendOnboarding(state: OnboardingDraft) {
    const profile: UserOnboardingProfile = {
      primary_goal: state.primaryGoal,
      preferred_scenario: state.preferredScenario,
      current_anxiety_level: state.anxietyLevel,
      practice_preference: "short_sentence_first",
      wants_pause_reminders: true,
      wants_auto_review: true,
      boundary_acknowledged: state.boundaryAcknowledged
    };
    await api.updateOnboardingProfile(currentUserId(), profile);
  }

  async function approveConsent() {
    if (!pendingConsent) {
      return;
    }
    setLoading(true);
    try {
      await api.respondToProtocol(
        pendingConsent.detail.protocol_id,
        currentUserId(),
        true
      );
      await savePreferencesWithConsent(
        pendingConsent.state,
        pendingConsent.detail.protocol_id
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法批准同意");
    } finally {
      setLoading(false);
    }
  }

  async function rejectConsent() {
    if (!pendingConsent) {
      return;
    }
    setLoading(true);
    try {
      await api.respondToProtocol(
        pendingConsent.detail.protocol_id,
        currentUserId(),
        false
      );
      setPendingConsent(null);
      setStatus("已完成引导。长期偏好没有保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消同意");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="开始前设置"
        description="用一分钟选择练习目标和偏好。这里只保存低敏结构化选项，不保存长文本。"
      />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel title="练习偏好">
          <form onSubmit={submit} className="space-y-4">
            <label className="block text-sm text-slate-700">
              主要目标
              <Select
                value={primaryGoal}
                onChange={(event) =>
                  setPrimaryGoal(event.target.value as OnboardingPrimaryGoal)
                }
                className="mt-1"
              >
                {goals.map((goal) => (
                  <option key={goal.value} value={goal.value}>
                    {goal.label}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block text-sm text-slate-700">
              想优先练习的情境
              <TextArea
                value={preferredScenario}
                maxLength={240}
                onChange={(event) => setPreferredScenario(event.target.value)}
                className="mt-1"
                placeholder="例如：小组讨论时表达不同意见"
              />
            </label>
            <label className="block text-sm text-slate-700">
              当前焦虑强度
              <div className="mt-2 flex items-center gap-3">
                <input
                  type="range"
                  min={1}
                  max={10}
                  value={anxietyLevel}
                  onChange={(event) => setAnxietyLevel(Number(event.target.value))}
                  className="w-full"
                />
                <Badge>{anxietyLevel}/10</Badge>
              </div>
            </label>
            <label className="flex gap-2 text-sm leading-6 text-slate-700">
              <input
                type="checkbox"
                checked={savePreferences}
                onChange={(event) => setSavePreferences(event.target.checked)}
              />
              保存低敏练习偏好，方便下次推荐练习难度和场景。
            </label>
            <label className="flex gap-2 text-sm leading-6 text-slate-700">
              <input
                type="checkbox"
                checked={boundaryAcknowledged}
                onChange={(event) => setBoundaryAcknowledged(event.target.checked)}
              />
              我了解 SocialEase 不是医疗产品，不做诊断；危机或安全担忧时应联系现实支持。
            </label>
            <Button type="submit" disabled={loading}>
              完成设置
            </Button>
          </form>
          <div className="mt-3">
            <ErrorBox message={error} />
            {status ? (
              <p className="mt-2 rounded-md border border-line bg-panel p-3 text-sm text-slate-700">
                {status}
              </p>
            ) : null}
          </div>
        </Panel>

        <div className="space-y-4">
          {pendingConsent ? (
            <DirectConsentCard
              detail={pendingConsent.detail}
              approving={loading}
              onApprove={approveConsent}
              onReject={rejectConsent}
            />
          ) : null}
          <Panel title="下一步">
            <div className="space-y-3 text-sm leading-6 text-slate-700">
              <p>
                完成后可以从安全对话开始，也可以直接进入角色扮演或社交练习计划。
              </p>
              <div className="flex flex-wrap gap-2">
                <LinkButton href="/chat">进入对话</LinkButton>
                <LinkButton href="/chat">在对话中选择练习</LinkButton>
                <LinkButton href="/chat">在对话中选择练习计划</LinkButton>
              </div>
              <FormHint>
                你可以在设置页修改或删除偏好和练习记录。
              </FormHint>
            </div>
          </Panel>
        </div>
      </div>
    </AuthGuard>
  );
}

function difficultyFromAnxiety(level: number) {
  if (level >= 8) {
    return 1;
  }
  if (level >= 5) {
    return 2;
  }
  return 3;
}

function LinkButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
    >
      {children}
    </Link>
  );
}

type OnboardingDraft = {
  primaryGoal: OnboardingPrimaryGoal;
  preferredScenario: string;
  anxietyLevel: number;
  savePreferences: boolean;
  boundaryAcknowledged: boolean;
};

type PendingOnboardingConsent = {
  detail: ConsentRequiredDetail;
  state: OnboardingDraft;
};

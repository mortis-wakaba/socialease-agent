"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_USER_ID } from "@/lib/constants";
import type { ExposurePlan, ExposureTask } from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea,
  TextInput
} from "@/components/ui";

export default function ProgressPage() {
  const [targetScenario, setTargetScenario] = useState("课堂发言");
  const [anxietyLevel, setAnxietyLevel] = useState(7);
  const [previousAttempts, setPreviousAttempts] = useState("写过开场白");
  const [plan, setPlan] = useState<ExposurePlan | null>(null);
  const [selectedTask, setSelectedTask] = useState<ExposureTask | null>(null);
  const [reflection, setReflection] = useState("完成后发现比想象中可控。");
  const [anxietyAfter, setAnxietyAfter] = useState(4);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api
      .getUserExposure(DEMO_USER_ID)
      .then((result) => setPlan(result.plan))
      .catch(() => undefined);
  }, []);

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setStatusMessage(null);
    try {
      const result = await api.createExposurePlan(
        DEMO_USER_ID,
        targetScenario,
        anxietyLevel,
        previousAttempts
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean)
      );
      if (result.blocked || !result.plan) {
        setPlan(null);
        setStatusMessage(result.response);
        return;
      }
      setPlan(result.plan);
      setSelectedTask(result.plan.tasks[0] ?? null);
      setStatusMessage(result.response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create plan");
    } finally {
      setLoading(false);
    }
  }

  async function completeTask(status: "completed" | "skipped" | "too_hard") {
    if (!selectedTask) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.completeExposureTask(
        DEMO_USER_ID,
        selectedTask.task_id,
        status,
        anxietyLevel,
        anxietyAfter,
        reflection
      );
      setPlan(result.plan);
      setSelectedTask(result.next_task);
      setStatusMessage(result.adjustment_reason);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update task");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Progress"
        description="Create a RAG-grounded social practice ladder and adjust the next task from completion feedback."
      />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Panel title="Create Plan">
            <form onSubmit={createPlan} className="space-y-3">
              <label className="block text-sm text-slate-600">
                Target scenario
                <TextInput
                  value={targetScenario}
                  onChange={(event) => setTargetScenario(event.target.value)}
                  className="mt-1"
                />
              </label>
              <label className="block text-sm text-slate-600">
                Current anxiety level
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
              <label className="block text-sm text-slate-600">
                Previous attempts
                <TextArea
                  value={previousAttempts}
                  onChange={(event) => setPreviousAttempts(event.target.value)}
                  className="mt-1 min-h-24"
                />
              </label>
              <Button type="submit" disabled={loading}>
                Create Ladder
              </Button>
            </form>
          </Panel>
          <ErrorBox message={error} />
          {statusMessage && (
            <Panel title="Update">
              <p className="text-sm leading-6 text-slate-700">{statusMessage}</p>
            </Panel>
          )}
        </div>

        <div className="space-y-4">
          {plan ? (
            <>
              <Panel title="Exposure Ladder">
                <div className="mb-3 rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
                  {plan.disclaimer}
                </div>
                <div className="space-y-3">
                  {plan.tasks.map((task) => {
                    const isRecommended =
                      task.task_id === plan.recommended_next_task_id;
                    const isSelected = task.task_id === selectedTask?.task_id;
                    return (
                      <button
                        key={task.task_id}
                        onClick={() => setSelectedTask(task)}
                        className={`w-full rounded-lg border p-3 text-left hover:border-brand ${
                          isSelected ? "border-brand bg-emerald-50" : "border-line bg-white"
                        }`}
                      >
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <span className="font-medium text-ink">{task.title}</span>
                          <Badge>difficulty {task.difficulty}/10</Badge>
                          {isRecommended && <Badge tone="good">recommended</Badge>}
                        </div>
                        <p className="text-sm leading-6 text-slate-700">
                          {task.description}
                        </p>
                        <p className="mt-2 text-xs text-slate-500">
                          {task.estimated_time_minutes} min · {task.success_criteria}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </Panel>

              {selectedTask && (
                <Panel title="Task Feedback">
                  <div className="space-y-3">
                    <div className="rounded-md border border-line bg-panel p-3">
                      <div className="font-medium text-ink">{selectedTask.title}</div>
                      <p className="mt-1 text-sm leading-6 text-slate-700">
                        {selectedTask.fallback_task}
                      </p>
                    </div>
                    <label className="block text-sm text-slate-600">
                      Anxiety after
                      <div className="mt-2 flex items-center gap-3">
                        <input
                          type="range"
                          min={1}
                          max={10}
                          value={anxietyAfter}
                          onChange={(event) => setAnxietyAfter(Number(event.target.value))}
                          className="w-full"
                        />
                        <Badge>{anxietyAfter}/10</Badge>
                      </div>
                    </label>
                    <TextArea
                      value={reflection}
                      onChange={(event) => setReflection(event.target.value)}
                      className="min-h-24"
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button onClick={() => completeTask("completed")} disabled={loading}>
                        Completed
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => completeTask("skipped")}
                        disabled={loading}
                      >
                        Skipped
                      </Button>
                      <Button
                        variant="danger"
                        onClick={() => completeTask("too_hard")}
                        disabled={loading}
                      >
                        Too hard
                      </Button>
                    </div>
                    <CitationList citations={selectedTask.citations} />
                  </div>
                </Panel>
              )}
            </>
          ) : (
            <Panel title="Exposure Ladder">
              <p className="text-sm text-slate-500">Create a plan to see the ladder.</p>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}


import Link from "next/link";
import { Badge, PageHeader, Panel } from "@/components/ui";

const primaryFlows = [
  {
    href: "/dashboard",
    title: "练习工作台",
    description: "查看当前计划、最近复盘和下一步建议，从普通用户视角继续练习。"
  },
  {
    href: "/onboarding",
    title: "开始前设置",
    description: "选择练习目标、偏好场景和当前强度，明确边界后再开始。"
  },
  {
    href: "/chat",
    title: "安全对话入口",
    description: "从一段社交压力描述开始，系统会先做安全判断，再路由到合适功能。"
  },
  {
    href: "/practice",
    title: "角色扮演练习",
    description: "选择课堂发言、宿舍沟通、拒绝请求等场景，练习表达并获得反馈。"
  },
  {
    href: "/progress",
    title: "社交练习计划",
    description: "生成由易到难的练习阶梯，完成后根据反馈调整下一步。"
  },
  {
    href: "/settings",
    title: "隐私和数据控制",
    description: "查看保存了哪些记录，导出或删除数据，并关闭长期练习偏好。"
  }
];

export default function HomePage() {
  return (
    <>
      <PageHeader
        title="SocialEase Agent"
        description="面向大学生社交压力场景的安全可控 Agent 系统。它不做诊断，不替代心理咨询，优先提供可解释、可退出、可追踪的练习流程。"
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="grid gap-4 sm:grid-cols-2">
          {primaryFlows.map((flow) => (
            <Link
              key={flow.href}
              href={flow.href}
              className="rounded-lg border border-line bg-white p-4 shadow-sm hover:border-brand"
            >
              <h2 className="font-semibold text-ink">{flow.title}</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">{flow.description}</p>
            </Link>
          ))}
        </div>

        <Panel title="使用边界">
          <div className="space-y-3 text-sm leading-6 text-slate-700">
            <div className="flex flex-wrap gap-2">
              <Badge tone="warn">非医疗产品</Badge>
              <Badge tone="good">先安全后练习</Badge>
              <Badge tone="info">支持导出/删除</Badge>
            </div>
            <p>
              如果出现自伤、自杀、伤害他人或严重危机表达，系统会暂停普通练习，
              并建议联系可信任的人、学校心理中心或当地紧急服务。
            </p>
            <Link
              href="/terms"
              className="inline-flex rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
            >
              查看试点知情说明
            </Link>
          </div>
        </Panel>
      </div>
    </>
  );
}

import { Badge, PageHeader, Panel } from "@/components/ui";
import Link from "next/link";

const sections = [
  {
    title: "非医疗边界",
    items: [
      "SocialEase Agent 不是医疗产品，不提供诊断结论。",
      "系统不能替代心理咨询、临床服务或紧急服务。",
      "系统只用于社交情境练习、结构化自助反思、练习计划、资源导航和安全升级提醒。"
    ]
  },
  {
    title: "危机边界",
    items: [
      "如果用户表达自伤、自杀、伤害他人、严重危机或迫近危险，普通练习流程应暂停。",
      "系统会建议联系可信任的人、学校心理中心或当地紧急服务。",
      "危机场景下系统不会承诺保密，也不会鼓励用户远离现实支持。"
    ]
  },
  {
    title: "数据和记忆",
    items: [
      "在后端策略支持的地方，原始社交或心理相关文本默认最小化保存。",
      "保存长期练习偏好前需要用户明确同意。",
      "用户可以在设置页导出练习记录、删除本人拥有的记录，或关闭长期练习偏好。"
    ]
  },
  {
    title: "试点参与",
    items: [
      "参与应是自愿的，并且可以退出。",
      "试点组织者应说明收集哪些数据、保留多久，以及如何退出试点。",
      "支持资源的联系方式应来自可信来源；系统不知道时应说明不知道。"
    ]
  }
];

export default function TermsPage() {
  return (
    <>
      <PageHeader
        title="试点知情说明"
        description="面向用户的 SocialEase Agent 试点边界说明。这里是产品安全界面的一部分，不是医疗同意书。"
      />
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Panel title="状态">
          <div className="space-y-3 text-sm leading-6 text-slate-700">
            <div className="flex flex-wrap gap-2">
              <Badge tone="info">试点说明</Badge>
              <Badge tone="warn">非医疗服务</Badge>
              <Badge tone="good">支持导出/删除</Badge>
            </div>
            <p>
              这份说明用于帮助你理解使用边界和数据控制。正式试点应提供清晰的组织者信息、
              退出方式和隐私说明。
            </p>
            <Link href="/privacy" className="inline-flex text-sm font-medium text-brand">
              查看隐私和数据说明
            </Link>
          </div>
        </Panel>

        <div className="space-y-4">
          {sections.map((section) => (
            <Panel key={section.title} title={section.title}>
              <ul className="space-y-2 text-sm leading-6 text-slate-700">
                {section.items.map((item) => (
                  <li key={item} className="rounded-md border border-line bg-white px-3 py-2">
                    {item}
                  </li>
                ))}
              </ul>
            </Panel>
          ))}
        </div>
      </div>
    </>
  );
}

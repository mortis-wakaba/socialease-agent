import { Badge, PageHeader, Panel } from "@/components/ui";

const dataRows = [
  {
    type: "账号数据",
    stored: "邮箱、密码哈希、会话 token 哈希",
    retention: "账号存在期间保留；logout 会撤销当前 session",
    control: "试点组织者可关闭公开注册；用户可退出试点"
  },
  {
    type: "练习记录",
    stored: "角色扮演、反思表、社交练习计划的最小化记录",
    retention: "用于展示进度和继续练习；用户可导出或删除",
    control: "设置页支持导出和删除本人记录"
  },
  {
    type: "Trace",
    stored: "安全分类、意图、权限动作、输出摘要和隐私策略结果",
    retention: "默认 30 天，可由 SOCIALEASE_TRACE_RETENTION_DAYS 配置",
    control: "过期后由 cleanup job 删除"
  },
  {
    type: "Protocol / Intervention Plan",
    stored: "同意协议状态、请求绑定、练习计划步骤状态",
    retention: "终态记录默认 30 天，可由 SOCIALEASE_PROTOCOL_RETENTION_DAYS 配置",
    control: "过期后由 cleanup job 删除"
  },
  {
    type: "长期偏好",
    stored: "低敏感度练习偏好，例如反馈风格和常用场景",
    retention: "只有明确同意后保存",
    control: "设置页可关闭长期偏好"
  },
  {
    type: "Agent Memory",
    stored: "经策略过滤或由用户确认的低敏摘要，与聊天历史分开",
    retention: "按类型设置期限；归档后默认不参与普通检索",
    control: "记忆中心可查看原因、编辑、归档、恢复或单条删除"
  }
];

const principles = [
  "SocialEase Agent 不是医疗产品，不做诊断，不替代心理咨询或紧急服务。",
  "心理健康相关文本默认走最小化和脱敏策略，不把系统设计成长期保存原始倾诉记录。",
  "危机表达会暂停普通练习，并建议联系可信任的人、学校心理中心或当地紧急服务。",
  "支持资源回答必须来自知识库引用；不知道时应说明不知道，不编造电话、学校或机构。",
  "正式试点应提供清晰的数据保留周期、退出方式和组织者联系方式。"
];

export default function PrivacyPage() {
  return (
    <>
      <PageHeader
        title="隐私和数据说明"
        description="说明 SocialEase Agent 在试点场景中如何最小化保存数据、支持用户控制，并保持非医疗边界。"
      />

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Panel title="边界">
          <div className="space-y-3 text-sm leading-6 text-slate-700">
            <div className="flex flex-wrap gap-2">
              <Badge tone="warn">非医疗产品</Badge>
              <Badge tone="good">导出/删除</Badge>
              <Badge tone="info">定期清理</Badge>
            </div>
            <p>
              本页说明当前数据保存和控制方式。使用前请确认你理解这些边界；
              如需更多说明，应联系试点组织者或可信任的现实支持。
            </p>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="原则">
            <ul className="space-y-2 text-sm leading-6 text-slate-700">
              {principles.map((item) => (
                <li key={item} className="rounded-md border border-line bg-white px-3 py-2">
                  {item}
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="数据分类和保留">
            <div className="overflow-x-auto">
              <table className="min-w-full border-separate border-spacing-y-2 text-left text-sm">
                <thead className="text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-3 py-2">类别</th>
                    <th className="px-3 py-2">保存内容</th>
                    <th className="px-3 py-2">保留方式</th>
                    <th className="px-3 py-2">用户控制</th>
                  </tr>
                </thead>
                <tbody>
                  {dataRows.map((row) => (
                    <tr key={row.type} className="bg-white">
                      <td className="rounded-l-md border-y border-l border-line px-3 py-3 font-medium text-slate-900">
                        {row.type}
                      </td>
                      <td className="border-y border-line px-3 py-3 text-slate-700">
                        {row.stored}
                      </td>
                      <td className="border-y border-line px-3 py-3 text-slate-700">
                        {row.retention}
                      </td>
                      <td className="rounded-r-md border-y border-r border-line px-3 py-3 text-slate-700">
                        {row.control}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </>
  );
}

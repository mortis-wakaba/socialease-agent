import type { RoleplayScenario } from "./types";

export const roleplayScenarios: Array<{
  id: RoleplayScenario;
  title: string;
  description: string;
}> = [
  {
    id: "classroom_speech",
    title: "课堂发言",
    description: "练习先说核心观点，再补充一个理由。"
  },
  {
    id: "group_discussion",
    title: "小组讨论",
    description: "练习表达判断、补充理由和接住追问。"
  },
  {
    id: "dorm_conflict",
    title: "宿舍沟通",
    description: "练习事实、影响、请求的表达顺序。"
  },
  {
    id: "club_icebreaking",
    title: "社团破冰",
    description: "练习轻量开场和延续对话。"
  },
  {
    id: "invite_classmate_meal",
    title: "约同学吃饭",
    description: "练习自然邀请和具体安排。"
  },
  {
    id: "ask_teacher_question",
    title: "向老师提问",
    description: "练习说明问题和已尝试步骤。"
  },
  {
    id: "interview_self_intro",
    title: "面试自我介绍",
    description: "练习简短、相关、清晰的介绍。"
  },
  {
    id: "refuse_request",
    title: "拒绝别人请求",
    description: "练习保持边界和表达理解。"
  },
  {
    id: "express_disagreement",
    title: "表达不同意见",
    description: "练习用具体例子表达不同看法。"
  }
];

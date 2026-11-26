/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  // 保留复核/交付引用工作流会回传派生字段，统一放开索引签名。
  [field: string]: any
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type RetentionReceipt = {
  回执编号: string
  receipt_no?: string
  出具时间?: string
  时间?: string
  经办人?: string
  k_id?: number
  批次编号?: string
  是否准予保留?: boolean
  retain?: boolean
  结论?: string
  有效依据?: string
  basis?: string
  有效引用?: string[]
}

export type RetentionEvent = {
  id: number
  时间: string
  类型: string
  kind?: string
  ok: boolean
  conflict?: boolean
  是否冲突?: boolean
  k_id?: number
  批次编号?: string
  p_id?: number
  引用编号?: string
  结论?: string
  说明?: string
  有效依据?: string
  回执编号?: string
  next_actions?: string[]
  下一次可执行的操作?: string[]
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow | null
  receipt?: RetentionReceipt | null
  event?: RetentionEvent | null
  conflict?: boolean
  next_actions?: string[]
  download_url?: string | null
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

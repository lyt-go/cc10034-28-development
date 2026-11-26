import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  OverviewResult,
  PageResult,
  RetentionEvent,
  RetentionReceipt,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ————————————————————————————————————————————
// 保留复核 / 交付引用台账
// ————————————————————————————————————————————

export async function listRetentionEvents(): Promise<RetentionEvent[]> {
  const response = await request('/api/retention/events')
  if (!response.ok) throw new Error('保留处理轨迹读取失败')
  const payload = (await response.json()) as { items: RetentionEvent[] }
  return payload.items
}

export async function listRetentionReceipts(): Promise<RetentionReceipt[]> {
  const response = await request('/api/retention/receipts')
  if (!response.ok) throw new Error('样本保留回执读取失败')
  const payload = (await response.json()) as { items: RetentionReceipt[] }
  return payload.items
}

export function sampleDownloadUrl(id: number): string {
  return `/api/k_anonymity/${id}/download-sample`
}

/** 阻断态下点击下载：读取服务端 403 文案并展示，不产生文件。 */
export async function fetchDownloadBlockReason(id: number): Promise<string> {
  const response = await request(sampleDownloadUrl(id))
  if (response.ok) return ''
  try {
    const data = (await response.json()) as { detail?: string; message?: string }
    return data.detail || data.message || '该样本当前不允许下载'
  } catch {
    return `接口返回 ${response.status}，该样本当前不允许下载`
  }
}

export type { EntryRow }

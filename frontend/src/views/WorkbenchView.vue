<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <section
      v-if="meta.key === 'k_anonymity'"
      class="rule-banner"
      data-testid="retention-rules"
    >
      <strong>样本保留复核：</strong>
      对将到期的样本批次提交「保留复核」，服务端校验假名化交付引用后出具保留回执，
      假名化页据此继续或暂停未完成的交付。已经超过保留期限、且查无有效引用的样本，
      将被禁止继续下载；即便交付具备继续资格，也不能再指向已清理的样本。
      「样本清理」与「引用续期」争用同一批次时给出冲突裁定，处理凭据与既往结论可在页尾回查。
    </section>
    <section
      v-else-if="meta.key === 'pseudonym'"
      class="rule-banner pseudo"
      data-testid="delivery-rules"
    >
      <strong>假名化交付引用：</strong>
      此处办理未结束引用的「引用续期」以及未完成交付的「继续交付 / 暂停交付」。
      继续交付必须存在有效引用与结论为准予保留的保留回执；
      指向已清理样本的交付一律不得继续。争用裁定与处理凭据可在页尾回查。
    </section>

    <div
      v-if="notice"
      class="notice"
      :class="notice.kind"
      data-testid="action-notice"
      :data-kind="notice.kind"
      :role="notice.kind === 'ok' ? 'status' : 'alert'"
    >
      <span data-testid="action-notice-text">{{ notice.text }}</span>
      <button class="notice-close" type="button" aria-label="关闭提示" @click="notice = null">×</button>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th v-if="showWorkflowCol">保留复核 / 交付引用</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>

          <td
            v-if="meta.key === 'k_anonymity'"
            class="wf-cell"
            :data-testid="`wf-${row.id}`"
            :data-sample-batch="String(!!row.is_sample_batch)"
            :data-expiry-state="String(row.到期状态 ?? '')"
          >
            <template v-if="row.is_sample_batch">
              <div class="wf-badges">
                <span
                  v-if="row.到期状态 && row.到期状态 !== row.status"
                  class="badge"
                  :class="expiryBadgeClass(String(row.到期状态))"
                  :data-testid="`expiry-${row.id}`"
                >{{ row.到期状态 }}</span>
                <span class="badge ok">有效引用 {{ Number(row.有效引用数 ?? 0) }}</span>
              </div>
              <div class="wf-line">保留期限：{{ row.保留期限 || '—' }}</div>
              <div v-if="row.清理时间" class="wf-line">清理时间：{{ row.清理时间 }}</div>
              <div v-if="row.有效引用" class="wf-line">有效引用：{{ row.有效引用 }}</div>
              <div v-if="row.最近回执编号" class="wf-line">
                样本保留回执：{{ row.最近回执编号 }}（{{ row.复核结论 }}）
              </div>
              <div v-if="row.有效依据" class="wf-basis" :data-testid="`basis-${row.id}`">
                有效依据：{{ row.有效依据 }}
              </div>
              <div class="wf-next" :data-testid="`next-actions-${row.id}`">
                下一次可执行的操作：{{ row.下一次可执行的操作 || '保留复核' }}
              </div>
            </template>
            <span v-else class="wf-plain">
              常规匿名检查记录，未纳入样本保留批次；点击本行「保留复核」可按样本批次办理。
            </span>
          </td>

          <td
            v-else-if="meta.key === 'pseudonym'"
            class="wf-cell"
            :data-testid="`wf-${row.id}`"
            :data-delivery-state="String(row.交付状态 ?? row.status ?? '')"
            :data-ref-state="String(row.引用状态 ?? '')"
          >
            <template v-if="row.is_delivery_ref">
              <div class="wf-badges">
                <span
                  class="badge"
                  :class="row.引用状态 === '有效' ? 'ok' : 'block'"
                  :data-testid="`ref-state-${row.id}`"
                >引用{{ row.引用状态 ?? '—' }}</span>
                <span
                  v-if="row.交付状态 && row.交付状态 !== row.status"
                  class="badge"
                  :class="deliveryBadgeClass(String(row.交付状态))"
                  :data-testid="`delivery-state-${row.id}`"
                >{{ row.交付状态 }}</span>
              </div>
              <div class="wf-line">引用编号：{{ row.引用编号 || '—' }}（关联批次 {{ row.关联批次编号 || '未关联' }}）</div>
              <div class="wf-line">有效期至：{{ row.引用有效期至 || row.失效时间 }}；续期 {{ Number(row.续期次数 ?? 0) }} 次</div>
              <div v-if="row.最近回执编号" class="wf-line">样本保留回执：{{ row.最近回执编号 }}</div>
              <div v-if="row.有效依据" class="wf-basis" :data-testid="`basis-${row.id}`">
                有效依据：{{ row.有效依据 }}
              </div>
              <div v-if="row.continue_block_reason" class="wf-block" :data-testid="`continue-block-${row.id}`">
                继续阻断：{{ row.continue_block_reason }}
              </div>
              <div class="wf-next" :data-testid="`next-actions-${row.id}`">
                下一次可执行的操作：{{ row.下一次可执行的操作 || '—' }}
              </div>
            </template>
            <span v-else class="wf-plain">
              常规假名映射，未登记为假名化交付引用；点击本行「引用续期」可纳入交付台账。
            </span>
          </td>

          <td class="row-actions">
            <template v-for="action in meta.actions" :key="action">
              <a
                v-if="action === '下载样本' && row.download_allowed"
                class="link action-as-link"
                role="button"
                :data-testid="`action-${row.id}-${action}`"
                :href="sampleDownloadUrl(Number(row.id))"
                download
              >{{ action }}</a>
              <button
                v-else
                class="link"
                type="button"
                :data-testid="`action-${row.id}-${action}`"
                :data-action="action"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2 + (showWorkflowCol ? 1 : 0)" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <RetentionHistory
      v-if="showWorkflowCol"
      :module-key="meta.key"
      :tick="historyTick"
    />

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <label v-if="meta.key === 'k_anonymity'" class="create-field">
          <span>保留期限（可选，YYYY-MM-DD；填入过去日期即为已到期批次）</span>
          <input
            v-model="createValues['保留期限']"
            data-testid="create-保留期限"
            placeholder="例如 2026-12-31"
          />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text" data-testid="footer-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import RetentionHistory from '@/components/RetentionHistory.vue'
import {
  createEntry,
  downloadEntries,
  fetchDownloadBlockReason,
  listEntries,
  moduleMeta,
  runAction as applyAction,
  sampleDownloadUrl,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref<{ kind: 'ok' | 'error' | 'conflict'; text: string } | null>(null)
const historyTick = ref(0)
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

const showWorkflowCol = computed(
  () => meta.key === 'k_anonymity' || meta.key === 'pseudonym',
)

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function expiryBadgeClass(state: string): string {
  if (state === '已清理') return 'block'
  if (state === '已到期') return 'warn'
  return 'ok'
}

function deliveryBadgeClass(state: string): string {
  if (state === '交付中') return 'ok'
  if (state === '已暂停') return 'warn'
  if (state === '已完成') return 'muted'
  return ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  if (meta.key === 'k_anonymity') createValues.value['保留期限'] = ''
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function showBlockedDownloadReason(row: EntryRow) {
  errorMessage.value = ''
  notice.value = null
  try {
    const reason = await fetchDownloadBlockReason(Number(row.id))
    const text = reason || String(row.block_reason || '该样本当前不允许下载')
    notice.value = { kind: 'error', text }
    errorMessage.value = text
  } catch (error) {
    const text = error instanceof Error ? error.message : '下载失败'
    notice.value = { kind: 'error', text }
    errorMessage.value = text
  } finally {
    historyTick.value += 1
    await reload()
  }
}

async function runAction(action: string, row: EntryRow) {
  if (action === '下载样本') {
    await showBlockedDownloadReason(row)
    return
  }
  errorMessage.value = ''
  notice.value = null
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) {
      const kind = result.conflict ? 'conflict' : 'error'
      notice.value = { kind, text: result.message }
      errorMessage.value = result.message
    } else {
      notice.value = { kind: 'ok', text: result.message }
      errorMessage.value = ''
    }
    await reload()
    historyTick.value += 1
  } catch (error) {
    const text = error instanceof Error ? error.message : '操作失败'
    notice.value = { kind: 'error', text }
    errorMessage.value = text
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.rule-banner {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1e3a8a;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.7;
  margin-bottom: 10px;
}
.rule-banner.pseudo {
  background: #f5f3ff;
  border-color: #ddd6fe;
  color: #4c1d95;
}
.rule-banner em {
  font-style: normal;
  font-weight: 600;
}
.notice {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 10px;
  border: 1px solid;
  white-space: pre-wrap;
}
.notice.ok {
  background: #f0fdf4;
  border-color: #bbf7d0;
  color: #166534;
}
.notice.error {
  background: #fef2f2;
  border-color: #fecaca;
  color: #991b1b;
}
.notice.conflict {
  background: #fffbeb;
  border-color: #fde68a;
  color: #92400e;
}
.notice-close {
  border: none;
  background: transparent;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  color: inherit;
  padding: 0 2px;
}
.wf-cell {
  min-width: 260px;
  max-width: 340px;
  font-size: 12px;
  vertical-align: top;
}
.wf-plain {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}
.wf-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 4px;
}
.badge {
  display: inline-block;
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
  background: #e2e8f0;
  color: #334155;
  white-space: nowrap;
}
.badge.ok {
  background: #dcfce7;
  color: #166534;
}
.badge.warn {
  background: #fef3c7;
  color: #92400e;
}
.badge.block {
  background: #fee2e2;
  color: #991b1b;
}
.badge.muted {
  background: #f1f5f9;
  color: #64748b;
}
.wf-line {
  color: #334155;
  margin-top: 2px;
  word-break: break-all;
}
.wf-basis {
  margin-top: 4px;
  background: #eff6ff;
  color: #1d4ed8;
  border-radius: 4px;
  padding: 4px 6px;
  word-break: break-all;
}
.wf-block {
  margin-top: 4px;
  background: #fef2f2;
  color: #991b1b;
  border-radius: 4px;
  padding: 4px 6px;
  word-break: break-all;
}
.wf-next {
  margin-top: 4px;
  color: #0f766e;
  font-weight: 600;
}
.row-actions {
  flex-wrap: wrap;
  min-width: 150px;
}
.action-as-link {
  cursor: pointer;
  text-decoration: none;
}
</style>

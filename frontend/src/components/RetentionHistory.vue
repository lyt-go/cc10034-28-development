<template>
  <section
    v-if="moduleKey === 'k_anonymity' || moduleKey === 'pseudonym'"
    class="retention-history"
    data-testid="retention-history"
  >
    <header class="rh-head">
      <div>
        <h3>既往结论回查 · 回执 / 冲突 / 处理凭据轨迹</h3>
        <p class="rh-sub">
          保留复核、引用续期、样本清理、冲突裁定，以及每次继续处理所采用的凭据，均只增留痕，可随时回查。
        </p>
      </div>
      <div class="rh-tools">
        <label class="rh-filter">
          <span>按批次筛查</span>
          <select v-model="batchFilter" data-testid="history-batch-filter">
            <option value="all">全部批次</option>
            <option value="4">BATCH-EXP-01（到期批次）</option>
            <option value="5">BATCH-NEW-01（保留期内）</option>
          </select>
        </label>
        <button class="btn" data-testid="history-refresh" type="button" @click="loadAll">
          刷新回查
        </button>
      </div>
    </header>

    <div v-if="loadError" class="rh-error" data-testid="history-error">{{ loadError }}</div>

    <div class="rh-grid">
      <div class="rh-col">
        <h4>样本保留回执（{{ receipts.length }}）</h4>
        <p v-if="!receipts.length" class="rh-empty" data-testid="receipt-empty">
          尚未出具回执，提交「保留复核」后在此回查。
        </p>
        <article
          v-for="receipt in reversedReceipts"
          :key="receipt.回执编号"
          class="receipt-card"
          :data-testid="`receipt-${receipt.回执编号}`"
          :data-retain="String(!!receipt.是否准予保留)"
        >
          <div class="receipt-title">
            <strong>《样本保留回执》{{ receipt.回执编号 }}</strong>
            <span class="badge" :class="receipt.是否准予保留 ? 'ok' : 'block'">
              {{ receipt.结论 }}
            </span>
          </div>
          <dl>
            <div><dt>批次编号</dt><dd>{{ receipt.批次编号 ?? '—' }}</dd></div>
            <div><dt>出具时间</dt><dd>{{ receipt.出具时间 ?? '—' }}</dd></div>
            <div><dt>经办人</dt><dd>{{ receipt.经办人 ?? '管理员' }}</dd></div>
            <div v-if="(receipt.有效引用 ?? []).length">
              <dt>验证有效引用</dt>
              <dd>{{ (receipt.有效引用 ?? []).join('、') }}</dd>
            </div>
            <div class="basis">
              <dt>有效依据</dt>
              <dd :data-testid="`receipt-basis-${receipt.回执编号}`">{{ receipt.有效依据 }}</dd>
            </div>
          </dl>
        </article>
      </div>

      <div class="rh-col">
        <h4>操作 / 冲突 / 结论轨迹（{{ events.length }}）</h4>
        <p v-if="!events.length" class="rh-empty" data-testid="event-empty">
          暂无处理记录。
        </p>
        <ol class="event-list">
          <li
            v-for="event in reversedEvents"
            :key="event.id"
            class="event-item"
            :data-testid="`history-event-${event.id}`"
            :data-ok="String(event.ok)"
            :data-conflict="String(!!event.conflict)"
          >
            <div class="event-line">
              <span class="event-time">{{ event.时间 }}</span>
              <span class="event-type" data-testid="event-type">{{ event.类型 }}</span>
              <span v-if="event.conflict" class="badge conflict" data-testid="event-conflict">
                冲突结果
              </span>
              <span v-else class="badge" :class="event.ok ? 'ok' : 'block'">
                {{ event.ok ? '成立' : '阻断' }}
              </span>
              <span v-if="event.批次编号" class="event-target">{{ event.批次编号 }}</span>
              <span v-if="event.引用编号" class="event-target">{{ event.引用编号 }}</span>
            </div>
            <p class="event-message" data-testid="event-message">{{ event.结论 }}</p>
            <p v-if="event.有效依据" class="event-basis" data-testid="event-basis">
              有效依据：{{ event.有效依据 }}
            </p>
          </li>
        </ol>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { listRetentionEvents, listRetentionReceipts } from '@/api/backend-service'
import type { RetentionEvent, RetentionReceipt } from '@/data/types'

const props = defineProps<{ moduleKey: string; tick?: number }>()

const events = ref<RetentionEvent[]>([])
const receipts = ref<RetentionReceipt[]>([])
const loadError = ref('')
const batchFilter = ref<'all' | '4' | '5'>('all')

const reversedEvents = computed(() => {
  const list = batchFilter.value === 'all'
    ? events.value
    : events.value.filter((event) => String(event.k_id ?? '') === batchFilter.value)
  return [...list].reverse()
})
const reversedReceipts = computed(() => {
  const list = batchFilter.value === 'all'
    ? receipts.value
    : receipts.value.filter((receipt) => String(receipt.k_id ?? '') === batchFilter.value)
  return [...list].reverse()
})

async function loadAll() {
  loadError.value = ''
  try {
    const [eventItems, receiptItems] = await Promise.all([
      listRetentionEvents(),
      listRetentionReceipts(),
    ])
    events.value = eventItems
    receipts.value = receiptItems
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '台账读取失败'
  }
}

watch(() => props.tick, () => {
  void loadAll()
})

onMounted(loadAll)
</script>

<style scoped>
.retention-history {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}
.rh-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.rh-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.rh-sub {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}
.rh-error {
  margin-top: 8px;
  color: #b42318;
  font-size: 13px;
}
.rh-tools {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-shrink: 0;
}
.rh-filter {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  color: var(--muted);
}
.rh-filter select {
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.rh-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.2fr);
  gap: 16px;
  margin-top: 12px;
}
.rh-col h4 {
  margin: 0 0 8px;
  font-size: 13px;
}
.rh-empty {
  color: var(--muted);
  font-size: 12px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 12px;
}
.receipt-card {
  border: 1px solid var(--border);
  border-left: 3px solid #16a34a;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #fcfdfd;
}
.receipt-card[data-retain='false'] {
  border-left-color: #dc2626;
}
.receipt-title {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
}
.receipt-card dl {
  margin: 0;
}
.receipt-card dl > div {
  display: grid;
  grid-template-columns: 84px 1fr;
  gap: 8px;
  font-size: 12px;
  margin-bottom: 4px;
}
.receipt-card dt {
  color: var(--muted);
}
.receipt-card dd {
  margin: 0;
  word-break: break-all;
}
.basis dd {
  background: #f1f5f9;
  border-radius: 4px;
  padding: 4px 6px;
}
.event-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 420px;
  overflow-y: auto;
}
.event-item {
  border-bottom: 1px solid #eef2f7;
  padding: 8px 2px;
}
.event-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.event-time {
  color: var(--muted);
}
.event-type {
  font-weight: 600;
}
.event-target {
  color: #334155;
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 6px;
}
.event-message {
  margin: 4px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
}
.event-basis {
  margin: 4px 0 0;
  font-size: 12px;
  color: #1d4ed8;
  background: #eff6ff;
  border-radius: 4px;
  padding: 4px 6px;
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
.badge.block {
  background: #fee2e2;
  color: #991b1b;
}
.badge.conflict {
  background: #fef3c7;
  color: #92400e;
}
@media (max-width: 980px) {
  .rh-grid {
    grid-template-columns: 1fr;
  }
}
</style>

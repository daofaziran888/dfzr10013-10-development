<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>经营分析报告管理</h2>
        <p class="page-desc">
          维护经营分析报告，并从同期对比差异数据集生成数据附件。附件保留周期、覆盖范围、
          汇总分子分母与来源版本；后续补报或重算不会改变已生成附件。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记分析报告</button>
        <button class="btn" type="button" @click="exportRows">导出经营分析报告清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openAttachments(row)">数据附件</button>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无经营分析报告数据，可先登记分析报告</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条经营分析报告记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="activeReport" class="panel" data-panel="report-attachments">
      <header class="panel-head">
        <div>
          <h3 class="panel-title">{{ activeReport.报告编号 }} · {{ activeReport.报告主题 }}</h3>
          <p class="page-desc">分析周期：{{ activeReport.分析周期 }} · 状态：{{ activeReport.报告状态 ?? activeReport.status }}</p>
        </div>
        <button class="btn ghost" type="button" @click="closeAttachments">关闭</button>
      </header>

      <div class="subsection">
        <h4>从同期对比数据集生成数据附件</h4>
        <label class="filter-item">
          <span>数据集版本</span>
          <select v-model="selectedDatasetId">
            <option :value="null">请选择已完成的对比数据集</option>
            <option v-for="item in datasets" :key="String(item.数据集id)" :value="item.数据集id">
              {{ item.数据集编号 }} · V{{ item.版本 }} · {{ item.基准周期 }} 对 {{ item.对比周期 }} · R{{ item.明细修订号 }}
            </option>
          </select>
        </label>
        <div class="panel-actions">
          <button class="btn primary" type="button" :disabled="!selectedDatasetId" @click="createAttachment">生成数据附件</button>
          <span v-if="attachmentMessage" :class="attachmentOk ? 'ok-text' : 'error-text'">{{ attachmentMessage }}</span>
        </div>
      </div>

      <div class="subsection">
        <h4>已生成附件（{{ attachmentList.length }} 份）</h4>
        <table class="data-table">
          <thead>
            <tr><th>数据集</th><th>版本</th><th>明细修订号</th><th>计算批次</th><th>摘要指纹</th><th>生成时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in attachmentList" :key="String(item.id)">
              <td>{{ item.数据集编号 }}</td>
              <td>V{{ item.数据集版本 }}</td>
              <td>R{{ item.明细修订号 }}</td>
              <td>{{ item.计算批次 }}</td>
              <td>{{ item.摘要指纹 }}</td>
              <td>{{ item.生成时间 }}</td>
              <td><button class="link" type="button" @click="showAttachment(item.id)">查看快照</button></td>
            </tr>
            <tr v-if="!attachmentList.length"><td colspan="7" class="empty-state">尚未生成数据附件</td></tr>
          </tbody>
        </table>
      </div>

      <div v-if="attachment" class="subsection" data-panel="attachment-detail">
        <h4>{{ attachment.数据集编号 }} · V{{ attachment.数据集版本 }} · 摘要 {{ attachment.摘要指纹 }}</h4>
        <p class="page-desc">
          基准 {{ attachment.周期?.基准周期?.文本 }}（{{ attachment.周期?.基准周期?.自然日数 }} 天）
          对 对比 {{ attachment.周期?.对比周期?.文本 }}（{{ attachment.周期?.对比周期?.自然日数 }} 天）
          · {{ attachment.对齐方式 }} · 对齐 {{ attachment.周期?.对齐?.对齐天数 }} 天
          · 来源批次 {{ attachment.计算批次 }} · 明细修订号 R{{ attachment.明细修订号 }}
          · 配置版本 V{{ attachment.来源版本?.配置版本 }}
        </p>
        <div v-if="(attachment.覆盖说明 ?? []).length" class="notice">
          <p v-for="line in attachment.覆盖说明" :key="line">{{ line }}</p>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>指标</th><th>类型</th><th>状态</th><th>基准值</th><th>对比值</th>
              <th>基准分子/分母</th><th>对比分子/分母</th><th>变化率</th><th>变化量</th><th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="metric in attachment.指标附件 ?? []" :key="metric.指标编码">
              <td>{{ metric.指标名称 }}</td>
              <td>{{ metric.指标类型 }}</td>
              <td>{{ metric.状态 }}</td>
              <td>{{ metric.基准值 }}</td>
              <td>{{ metric.对比值 }}</td>
              <td>{{ metric.基准分子 ?? '—' }} / {{ metric.基准分母 ?? '—' }}</td>
              <td>{{ metric.对比分子 ?? '—' }} / {{ metric.对比分母 ?? '—' }}</td>
              <td>{{ metric.变化率 ?? '—' }}</td>
              <td>{{ metric.变化量 ?? '—' }}</td>
              <td class="cell-note">{{ metric.变化说明 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/report'
const columns = ['报告编号', '报告主题', '分析周期', '报告模板', '关键发现', '数据附件', '分发对象', '报告状态']
const actions = ['生成报告', '分发报告', '归档报告']
const statuses = ['草稿', '已生成', '已分发', '已归档']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref([
  { label: '分析报告总数', value: 0 },
  { label: '已生成报告', value: 0 },
  { label: '数据附件份数', value: 0 },
])

const activeReport = ref<Row | null>(null)
const datasets = ref<Row[]>([])
const selectedDatasetId = ref<number | null>(null)
const attachmentList = ref<Row[]>([])
const attachment = ref<Row | null>(null)
const attachmentMessage = ref('')
const attachmentOk = ref(false)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '分析报告登记入口尚未接入审批流'
}

async function openAttachments(row: Row) {
  attachmentMessage.value = ''
  attachment.value = null
  activeReport.value = await fetchJson<Row>(`${ENDPOINT}/${row.id}`)
  attachmentList.value = activeReport.value.数据附件清单 ?? []
  try {
    const payload = await fetchJson<{ items: Row[] }>('/api/compare/datasets')
    datasets.value = (payload.items ?? []).filter((item) => item.状态 === '已完成')
    selectedDatasetId.value = datasets.value.length ? datasets.value[datasets.value.length - 1].数据集id : null
  } catch {
    datasets.value = []
  }
}

function closeAttachments() {
  activeReport.value = null
  attachment.value = null
  attachmentList.value = []
}

async function createAttachment() {
  if (!activeReport.value || !selectedDatasetId.value) return
  attachmentMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${activeReport.value.id}/attachments`, {
      method: 'POST',
      body: JSON.stringify({ dataset_id: selectedDatasetId.value, operator: '值班管理员' }),
    })
    const payload = await response.json()
    attachmentOk.value = Boolean(payload.ok)
    attachmentMessage.value = payload.message ?? ''
    if (payload.ok) {
      activeReport.value = await fetchJson<Row>(`${ENDPOINT}/${activeReport.value.id}`)
      attachmentList.value = activeReport.value.数据附件清单 ?? []
      await reload()
      if (payload.entry?.附件id) await showAttachment(payload.entry.附件id)
    }
  } catch (error) {
    attachmentOk.value = false
    attachmentMessage.value = error instanceof Error ? error.message : '数据附件生成失败'
  }
}

async function showAttachment(attachmentId: number) {
  attachment.value = await fetchJson<Row>(`${ENDPOINT}/attachments/${attachmentId}`)
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message ?? '经营分析报告动作未生效')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经营分析报告操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const payload = await fetchJson<{ items: Row[]; total: number }>(`${ENDPOINT}?${query}`)
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = [
      { label: '分析报告总数', value: total.value },
      { label: '已生成报告', value: rows.value.filter((row) => ['已生成', '已分发', '已归档'].includes(row.status)).length },
      { label: '数据附件份数', value: attachmentList.value.length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经营分析报告列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin: 14px 0; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.panel-title { margin: 0; font-size: 15px; }
.panel-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.subsection { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 10px; }
.subsection h4 { margin: 0 0 6px; font-size: 14px; }
.subsection select { min-width: 360px; padding: 5px 6px; border: 1px solid var(--border); border-radius: 5px; }
.notice { background: #f8fafc; border: 1px dashed var(--border); border-radius: 6px; padding: 8px 10px; margin: 8px 0; font-size: 12px; }
.notice p { margin: 2px 0; }
.cell-note { max-width: 260px; font-size: 12px; color: var(--muted); }
.ok-text { color: #17663a; }
</style>

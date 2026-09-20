import { fetchGrouping } from '../../../api/grouping.js'
import DataTable from '../../../components/common/DataTable.jsx'
import Pagination from '../../../components/common/Pagination.jsx'
import Tag from '../../../components/common/Tag.jsx'
import { ErrorState } from '../../../components/common/Feedback.jsx'
import { DATA_SOURCE_TONE, EXCEEDANCE_STATUS_TONE, EXCEEDANCE_STATUS_LABELS } from '../../../constants/index.js'
import { useListQuery } from '../../../hooks/useListQuery.js'
import { formatDateTime, formatNumber } from '../../../utils/format.js'

/**
 * 单条记录层: 可挂在时间段行下内联展开, 也可在抽屉中按任意分组路径查看。
 * 计数 (totals) 由后端按整层计算, 翻页时保持稳定。
 */
export default function RecordsPanel({ path, filters, granularity, onOpenRecord }) {
  const query = useListQuery(
    fetchGrouping,
    { ...filters, ...path, level: 'record', granularity },
    { pageSize: 10 }
  )
  const totals = query.data?.totals

  const columns = [
    {
      key: 'measured_at',
      title: '监测时间',
      className: 'cell-nowrap',
      render: (row) => formatDateTime(row.measured_at)
    },
    {
      key: 'station',
      title: '监测点',
      render: (row) => `${row.station?.code || ''} ${row.station?.name || ''}`
    },
    { key: 'pollutant_label', title: '因子', className: 'cell-nowrap' },
    { key: 'period_label', title: '周期', className: 'cell-nowrap' },
    {
      key: 'value',
      title: '监测值',
      align: 'right',
      render: (row) => (
        <span className={row.is_exceeded ? 'danger-text strong' : ''}>
          {formatNumber(row.value)} <span className="muted small">{row.unit}</span>
        </span>
      )
    },
    {
      key: 'limit_value',
      title: '限值',
      align: 'right',
      render: (row) => (row.limit_value === null ? '无限值' : formatNumber(row.limit_value))
    },
    {
      key: 'is_exceeded',
      title: '超标',
      render: (row) => (row.is_exceeded ? <Tag tone="danger">是</Tag> : <Tag tone="success">否</Tag>)
    },
    {
      key: 'exceedance_status',
      title: '标注状态',
      render: (row) =>
        row.exceedance_status ? (
          <Tag tone={EXCEEDANCE_STATUS_TONE[row.exceedance_status]}>
            {EXCEEDANCE_STATUS_LABELS[row.exceedance_status] || row.exceedance_status}
          </Tag>
        ) : (
          <span className="muted">-</span>
        )
    },
    {
      key: 'data_source_label',
      title: '来源',
      render: (row) => <Tag tone={DATA_SOURCE_TONE[row.data_source]}>{row.data_source_label}</Tag>
    },
    { key: 'recorder', title: '录入人', render: (row) => row.recorder || '-' },
    {
      key: 'actions',
      title: '操作',
      render: (row) => (
        <button
          type="button"
          className="btn btn-sm"
          onClick={(event) => {
            event.stopPropagation()
            onOpenRecord(row.id)
          }}
        >
          详情
        </button>
      )
    }
  ]

  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />

  return (
    <div className="group-records">
      <DataTable
        columns={columns}
        rows={query.items}
        loading={query.loading}
        emptyText="该分组下没有监测记录"
        emptyIcon="🧾"
        onRowClick={(row) => onOpenRecord(row.id)}
        caption={
          totals
            ? `共 ${totals.count} 条记录 · 超标 ${totals.exceeded_count} 条 · 点击行可查看录入内容与判定结果`
            : ''
        }
      />
      <Pagination
        page={query.page}
        pages={query.pages}
        total={query.total}
        pageSize={query.pageSize}
        onPageChange={query.setPage}
        onPageSizeChange={query.setPageSize}
      />
    </div>
  )
}

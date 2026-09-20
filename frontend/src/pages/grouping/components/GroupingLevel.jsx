import { useState } from 'react'
import { fetchGrouping } from '../../../api/grouping.js'
import Pagination from '../../../components/common/Pagination.jsx'
import Tag from '../../../components/common/Tag.jsx'
import { EmptyState, ErrorState, Loading } from '../../../components/common/Feedback.jsx'
import { useListQuery } from '../../../hooks/useListQuery.js'
import { formatNumber, formatPercent } from '../../../utils/format.js'
import RecordsPanel from './RecordsPanel.jsx'

/** 各层级的展示配置: 子层级、行 -> 子层路径、层级特有列 */
const LEVEL_CONFIG = {
  area: {
    name: '片区',
    childLevel: 'station',
    childName: '点位',
    childPath: (row) => ({ group_area: row.key }),
    extraColumns: [
      { key: 'station_count', title: '点位数', align: 'right', render: (row) => row.station_count }
    ]
  },
  station: {
    name: '点位',
    childLevel: 'pollutant',
    childName: '因子',
    childPath: (row) => ({ group_station_id: row.key }),
    extraColumns: [
      {
        key: 'station_type_label',
        title: '类型 / 状态',
        render: (row) => `${row.station_type_label} / ${row.status_label}`
      }
    ]
  },
  pollutant: {
    name: '因子',
    childLevel: 'bucket',
    childName: '时间段',
    childPath: (row) => ({ group_pollutant: row.key }),
    extraColumns: [
      { key: 'avg_value', title: '均值', align: 'right', render: (row) => formatNumber(row.avg_value) },
      { key: 'max_value', title: '最大值', align: 'right', render: (row) => formatNumber(row.max_value) }
    ]
  },
  bucket: {
    name: '时间段',
    childLevel: 'record',
    childName: '记录',
    childPath: (row) => ({ group_bucket: row.key }),
    extraColumns: []
  }
}

/**
 * 递归分组层级: 展开行时按需加载下一层, 每层独立分页。
 * 计数一致性由后端保证 (父级 count == 本层 totals.count), 这里直接对照展示。
 * 注意: useListQuery 的筛选参数只在挂载时生效, 筛选条件变化时由页面通过 key 重建整棵树。
 */
export default function GroupingLevel({ level, path, filters, granularity, onOpenRecords, onOpenRecord }) {
  const config = LEVEL_CONFIG[level]
  const query = useListQuery(
    fetchGrouping,
    { ...filters, ...path, level, granularity },
    { pageSize: 20 }
  )
  const [expanded, setExpanded] = useState({})

  const toggle = (key) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))

  if (query.loading && query.items.length === 0) return <Loading text="正在加载分组..." />
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />
  if (query.items.length === 0) {
    return <EmptyState text="该分组下没有子分组" icon="🗂️" />
  }

  const data = query.data
  const parent = data?.parent
  const totals = data?.totals
  const breadcrumb = data?.breadcrumb ?? []
  const consistent = parent ? parent.count === totals?.count : true
  const columnCount = 1 + config.extraColumns.length + 5

  const openRecords = (row) => {
    const recordPath = { ...path, ...config.childPath(row) }
    const rowBreadcrumb = [...breadcrumb, { level, key: row.key, label: row.label }]
    onOpenRecords(recordPath, rowBreadcrumb)
  }

  return (
    <div className="group-level">
      <div className={`group-consistency ${consistent ? 'is-ok' : 'is-bad'}`}>
        {parent ? (
          <>
            父级{LEVEL_CONFIG[parent.level]?.name || ''}「{parent.label}」共 <b>{parent.count}</b> 条 ·
            本层 {query.total} 个{config.name}分组合计 <b>{totals?.count ?? 0}</b> 条
            {consistent ? ' · ✓ 计数一致' : ' · ✗ 计数不一致, 请刷新重试'}
          </>
        ) : (
          <>
            共 {query.total} 个{config.name}分组 · 合计 <b>{totals?.count ?? 0}</b> 条记录 ·
            超标 <b>{totals?.exceeded_count ?? 0}</b> 条
          </>
        )}
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>{config.name}</th>
              {config.extraColumns.map((column) => (
                <th key={column.key} className={column.align === 'right' ? 'text-right' : ''}>
                  {column.title}
                </th>
              ))}
              <th className="text-right">数据条数</th>
              <th className="text-right">超标数</th>
              <th className="text-right">超标率</th>
              <th className="text-right">{config.childName}数</th>
              <th style={{ width: 96 }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {query.items.map((row) => {
              const isOpen = Boolean(expanded[row.key])
              const childPath = { ...path, ...config.childPath(row) }
              return [
                <tr key={row.key} className={row.is_empty ? 'group-row-empty' : ''}>
                  <td>
                    <div className="group-labelcell">
                      <button
                        type="button"
                        className="group-expander"
                        onClick={() => toggle(row.key)}
                        aria-label={isOpen ? '收起' : '展开'}
                      >
                        {isOpen ? '▾' : '▸'}
                      </button>
                      <span>{row.label}</span>
                      {row.is_empty ? <Tag tone="neutral">空分组</Tag> : null}
                    </div>
                  </td>
                  {config.extraColumns.map((column) => (
                    <td key={column.key} className={column.align === 'right' ? 'text-right' : ''}>
                      {column.render(row)}
                    </td>
                  ))}
                  <td className="text-right strong">{row.count}</td>
                  <td className={`text-right ${row.exceeded_count ? 'danger-text' : ''}`}>
                    {row.exceeded_count}
                  </td>
                  <td className="text-right">{formatPercent(row.exceed_rate)}</td>
                  <td className="text-right muted">{row.children_total}</td>
                  <td>
                    <button type="button" className="btn btn-sm" onClick={() => openRecords(row)}>
                      查看记录
                    </button>
                  </td>
                </tr>,
                isOpen ? (
                  <tr key={`${row.key}__children`} className="group-child-row">
                    <td colSpan={columnCount}>
                      <div className="group-child">
                        {config.childLevel === 'record' ? (
                          <RecordsPanel
                            path={childPath}
                            filters={filters}
                            granularity={granularity}
                            onOpenRecord={onOpenRecord}
                          />
                        ) : (
                          <GroupingLevel
                            level={config.childLevel}
                            path={childPath}
                            filters={filters}
                            granularity={granularity}
                            onOpenRecords={onOpenRecords}
                            onOpenRecord={onOpenRecord}
                          />
                        )}
                      </div>
                    </td>
                  </tr>
                ) : null
              ]
            })}
          </tbody>
        </table>
      </div>
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

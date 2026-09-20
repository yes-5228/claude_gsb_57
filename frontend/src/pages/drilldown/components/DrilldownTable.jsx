import { useCallback, useEffect, useState } from 'react'
import { drilldown } from '../../../api/drilldown.js'
import { Alert, Loading } from '../../../components/common/Feedback.jsx'
import Tag from '../../../components/common/Tag.jsx'
import Pagination from '../../../components/common/Pagination.jsx'
import { formatNumber, formatPercent } from '../../../utils/format.js'

const NEXT_LEVEL = {
  area: { level: 'station', label: '点位' },
  station: { level: 'pollutant', label: '因子' },
  pollutant: { level: 'bucket', label: '时间段' },
  bucket: { level: 'record', label: '记录' }
}

const BUCKET_PAGE_SIZE = 20

/** 把节点路径追加到请求参数上. */
function nodeParams(baseFilters, path, granularity) {
  const params = { ...baseFilters }
  if (path.area) params.area = path.area
  if (path.station_id) params.station_id = path.station_id
  if (path.pollutant) params.pollutant_code = path.pollutant
  if (path.bucket) params.bucket = path.bucket
  if (granularity) params.granularity = granularity
  return params
}

/** 从某行节点构造给 record 层的路径. */
function recordPath(nodePath, level, item) {
  const next = { ...nodePath }
  if (level === 'area') next.area = item.key
  if (level === 'station') next.station_id = item.station_id
  if (level === 'pollutant') next.pollutant = item.pollutant
  if (level === 'bucket') next.bucket = item.bucket
  return next
}

function NodeRow({
  level,
  item,
  depth,
  path,
  granularity,
  baseFilters,
  expandedKey,
  onToggle,
  onOpenRecords
}) {
  const hasChildren = level !== 'record'
  const expanded = expandedKey === item.key
  const next = NEXT_LEVEL[level]
  // 时间段之下即原始记录, 直接打开记录抽屉, 不再在表格内嵌记录行
  const opensRecordsDirectly = level === 'bucket'

  const handleExpand = (node) => {
    if (opensRecordsDirectly) {
      onOpenRecords(recordPath(path, level, node), node.label)
    } else {
      onToggle(node)
    }
  }

  return (
    <>
      <tr className={hasChildren && item.count > 0 ? 'clickable' : ''}>
        <td style={{ paddingLeft: 12 + depth * 22 }}>
          <span className="inline">
            {hasChildren ? (
              <button
                type="button"
                className="drill-toggle"
                aria-label={opensRecordsDirectly ? '查看记录' : expanded ? '收起' : '展开'}
                disabled={item.count === 0}
                title={
                  item.count === 0
                    ? '空分组, 没有可查看的记录'
                    : opensRecordsDirectly
                      ? '打开该时间段的原始记录'
                      : `展开到${next.label}`
                }
                onClick={() => handleExpand(item)}
              >
                {opensRecordsDirectly ? '≣' : expanded ? '▾' : '▸'}
              </button>
            ) : (
              <span className="drill-toggle drill-leaf" />
            )}
            <span className={item.is_empty ? 'drill-empty-label' : 'strong'}>{item.label}</span>
            {item.is_empty ? <Tag tone="neutral">空分组</Tag> : null}
            {item.code ? <span className="hint mono">{item.code}</span> : null}
            {item.status_label ? (
              <Tag tone={item.status === 'active' ? 'success' : 'warning'}>{item.status_label}</Tag>
            ) : null}
          </span>
        </td>
        <td className="text-right strong">
          {item.count}
          {item.is_empty ? <span className="hint"> 条</span> : null}
        </td>
        <td className="text-right">
          {item.exceeded_count ? <span className="danger-text">{item.exceeded_count}</span> : '0'}
        </td>
        <td className="text-right">{formatPercent(item.exceed_rate)}</td>
        <td className="text-right">
          {level === 'pollutant' || level === 'bucket' ? formatNumber(item.avg_value) : '-'}
          {item.unit ? <span className="hint"> {item.unit}</span> : null}
        </td>
        <td className="text-right">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onOpenRecords(recordPath(path, level, item), item.label)}
            title="直接打开该分组下的原始记录"
          >
            查看记录
          </button>
        </td>
      </tr>
      {expanded && hasChildren && !opensRecordsDirectly ? (
        <ChildRows
          level={next.level}
          depth={depth + 1}
          parentPath={recordPath(path, level, item)}
          parentCount={item.count}
          granularity={granularity}
          baseFilters={baseFilters}
          onOpenRecords={onOpenRecords}
        />
      ) : null}
    </>
  )
}

function ChildRows({
  level,
  depth,
  parentPath,
  parentCount,
  granularity,
  baseFilters,
  onOpenRecords
}) {
  const isBucket = level === 'bucket'
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedKey, setExpandedKey] = useState(null)

  const params = useCallback(() => {
    const nextParams = nodeParams(baseFilters, parentPath, granularity)
    if (isBucket) {
      nextParams.page = page
      nextParams.page_size = BUCKET_PAGE_SIZE
    }
    return nextParams
  }, [baseFilters, parentPath, granularity, isBucket, page])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const payload = await drilldown(level, params())
      setData(payload)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [level, params])

  useEffect(() => {
    load()
  }, [load])

  if (loading && !data) {
    return (
      <tr>
        <td colSpan={6} style={{ paddingLeft: 12 + depth * 22 }}>
          <Loading text="正在加载子分组..." />
        </td>
      </tr>
    )
  }

  return (
    <>
      {error ? (
        <tr>
          <td colSpan={6} style={{ paddingLeft: 12 + depth * 22 }}>
            <Alert tone="error">{error.message}</Alert>
          </td>
        </tr>
      ) : null}
      {data && data.items.length === 0 ? (
        <tr>
          <td colSpan={6} style={{ paddingLeft: 12 + depth * 22 }}>
            <Tag tone="neutral">该分组下无数据 (空)</Tag>
          </td>
        </tr>
      ) : null}
      {data?.items.map((item) => (
        <NodeRow
          key={`${level}-${item.key}`}
          level={level}
          item={item}
          depth={depth}
          path={parentPath}
          granularity={granularity}
          baseFilters={baseFilters}
          expandedKey={expandedKey}
          onToggle={(node) => setExpandedKey((current) => (current === node.key ? null : node.key))}
          onOpenRecords={onOpenRecords}
        />
      ))}
      {data && isBucket ? (
        <tr>
          <td colSpan={6} style={{ paddingLeft: 12 + depth * 22 }}>
            <BucketPager
              data={data}
              onPageChange={setPage}
            />
          </td>
        </tr>
      ) : null}
      {data ? (
        <tr>
          <td colSpan={6} style={{ paddingLeft: 12 + depth * 22 }}>
            <CountCheck data={data} parentCount={parentCount} level={level} />
          </td>
        </tr>
      ) : null}
    </>
  )
}

function BucketPager({ data, onPageChange }) {
  return (
    <div className="drill-pager">
      <Pagination
        page={data.page}
        pages={data.pages}
        total={data.bucket_universe_size}
        pageSize={data.page_size}
        onPageChange={onPageChange}
      />
      <div className="hint" style={{ padding: '0 18px 10px' }}>
        时间桶按 {data.granularity === 'month' ? '月' : '日'} 分页, 共 {data.bucket_universe_size} 个桶
        (含 {data.empty_count} 个空桶) · 全集记录数 {data.children_count} 条, 翻页保持稳定
      </div>
    </div>
  )
}

/** 子层条数 vs 父层计数的一致性提示. */
function CountCheck({ data, parentCount, level }) {
  const children = data.children_count
  const consistent = children === parentCount && data.consistent
  const levelLabels = { station: '点位', pollutant: '因子', bucket: '时间段' }
  return (
    <div className={`drill-check ${consistent ? 'ok' : 'bad'}`}>
      {consistent ? (
        <>✓ {levelLabels[level]}层合计 {children} 条 = 上层计数 {parentCount} 条
        {level === 'bucket'
          ? ` (时间桶全集 ${data.bucket_universe_size} 个, 空桶已显式标出)`
          : ` (其中空分组 ${data.empty_count} 个, 已显式标出)`}
        </>
      ) : (
        <>✗ 计数不一致: {levelLabels[level]}层合计 {children} 条 ≠ 上层 {parentCount} 条, 请刷新后重试</>
      )}
    </div>
  )
}

export default function DrilldownTable({ baseFilters, granularity, rootData, rootLoading, rootError, onOpenRecords }) {
  const [expandedKey, setExpandedKey] = useState(null)

  // 筛选条件或时间粒度变化后, 根层数据会刷新; 重置片区展开状态, 避免路径与新筛选错配
  useEffect(() => {
    setExpandedKey(null)
  }, [baseFilters, granularity])

  return (
    <div className="table-wrap">
      {rootError ? <Alert tone="error">{rootError.message}</Alert> : null}
      {rootLoading && !rootData ? <Loading text="正在汇总片区数据..." /> : null}
      {rootData ? (
        <table className="data-table drill-table">
          <thead>
            <tr>
              <th>分组 (片区 → 点位 → 因子 → 时间段)</th>
              <th className="text-right">记录数</th>
              <th className="text-right">超标数</th>
              <th className="text-right">超标率</th>
              <th className="text-right">均值</th>
              <th className="text-right">原始记录</th>
            </tr>
          </thead>
          <tbody>
            {rootData.items.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <div className="empty">当前筛选范围内没有任何片区数据</div>
                </td>
              </tr>
            ) : null}
            {rootData.items.map((item) => (
              <NodeRow
                key={`area-${item.key}`}
                level="area"
                item={item}
                depth={0}
                path={{ area: null, station_id: null, pollutant: null, bucket: null }}
                granularity={granularity}
                baseFilters={baseFilters}
                expandedKey={expandedKey}
                onToggle={(node) => setExpandedKey((current) => (current === node.key ? null : node.key))}
                onOpenRecords={onOpenRecords}
              />
            ))}
          </tbody>
          {rootData.items.length > 0 ? (
            <tfoot>
              <tr className="drill-foot">
                <td>
                  <span className="strong">片区层合计</span>
                  <span className="hint"> (共 {rootData.items.length} 个片区, 其中 {rootData.empty_count} 个空片区)</span>
                </td>
                <td className="text-right strong">{rootData.children_count}</td>
                <td className="text-right">
                  {sumBy(rootData.items, 'exceeded_count')}
                </td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          ) : null}
        </table>
      ) : null}
    </div>
  )
}

function sumBy(items, key) {
  return items.reduce((acc, item) => acc + (item[key] || 0), 0)
}

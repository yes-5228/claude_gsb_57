import { useCallback, useEffect, useState } from 'react'
import { drilldown, fetchMeasurementDetail } from '../../../api/drilldown.js'
import Modal from '../../../components/common/Modal.jsx'
import Tag from '../../../components/common/Tag.jsx'
import Pagination from '../../../components/common/Pagination.jsx'
import { Alert, Loading } from '../../../components/common/Feedback.jsx'
import {
  EXCEEDANCE_LEVEL_TONE,
  EXCEEDANCE_STATUS_LABELS,
  EXCEEDANCE_STATUS_TONE
} from '../../../constants/index.js'
import { formatDateTime, formatNumber, formatPercent } from '../../../utils/format.js'

const PAGE_SIZE = 10

/**
 * 任意层级"查看原始记录": 携带当前路径 + 基础筛选拉取 record 层,
 * 列表自身分页; 点击单条可再打开当时的录入内容与超标判定详情。
 */
export default function RecordDrawer({ open, title, params, onClose }) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [detailId, setDetailId] = useState(null)

  const load = useCallback(async () => {
    if (!open) return
    setLoading(true)
    setError(null)
    try {
      const payload = await drilldown('record', { ...params, page, page_size: PAGE_SIZE })
      setData(payload)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [open, params, page])

  useEffect(() => {
    if (open) {
      setPage(1)
      setDetailId(null)
    }
  }, [open, params])

  useEffect(() => {
    load()
  }, [load])

  return (
    <Modal
      open={open && detailId === null}
      drawer
      title={`原始记录 · ${title || '全部范围'}`}
      onClose={onClose}
      footer={<button type="button" className="btn" onClick={onClose}>关闭</button>}
    >
      <div className="stack">
        <div className="hint">
          共 <span className="strong">{data?.total_count ?? '-'}</span> 条记录, 翻页期间计数保持稳定
        </div>
        {error ? <Alert tone="error">{error.message}</Alert> : null}
        {loading && !data ? <Loading text="正在加载原始记录..." /> : null}
        {data ? (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>监测时间</th>
                    <th>点位</th>
                    <th>因子</th>
                    <th className="text-right">监测值</th>
                    <th>判定</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="text-center text-muted" style={{ padding: 24 }}>
                        当前范围下没有原始记录
                      </td>
                    </tr>
                  ) : null}
                  {data.items.map((row) => (
                    <tr
                      key={row.id}
                      className="clickable"
                      onClick={() => setDetailId(row.id)}
                      title="点击查看录入详情"
                    >
                      <td className="cell-nowrap">{formatDateTime(row.measured_at)}</td>
                      <td>{row.station ? `${row.station.code} ${row.station.name}` : row.station_id}</td>
                      <td>{row.pollutant_label}</td>
                      <td className="text-right strong">
                        {formatNumber(row.value)} <span className="text-muted">{row.unit}</span>
                      </td>
                      <td>
                        {row.is_exceeded ? (
                          <Tag tone={EXCEEDANCE_LEVEL_TONE[row.exceedance?.level] || 'danger'}>
                            超标 {row.exceedance?.level_label || ''}
                          </Tag>
                        ) : (
                          <Tag tone="success">达标</Tag>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              page={data.page}
              pages={data.pages}
              total={data.total_count}
              pageSize={data.page_size}
              onPageChange={setPage}
            />
          </>
        ) : null}
      </div>

      {detailId !== null ? (
        <MeasurementDetail measurementId={detailId} onBack={() => setDetailId(null)} />
      ) : null}
    </Modal>
  )
}

function MeasurementDetail({ measurementId, onBack }) {
  const [row, setRow] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    setLoading(true)
    fetchMeasurementDetail(measurementId)
      .then((payload) => active && setRow(payload))
      .catch((err) => active && setError(err))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [measurementId])

  return (
    <Modal
      open
      drawer
      title="单条记录详情"
      onClose={onBack}
      footer={<button type="button" className="btn" onClick={onBack}>返回记录列表</button>}
    >
      {loading ? <Loading text="正在加载记录详情..." /> : null}
      {error ? <Alert tone="error">{error.message}</Alert> : null}
      {row ? (
        <div className="stack">
          <div className="inline">
            <h3 style={{ margin: 0 }}>
              {row.station ? `${row.station.code} ${row.station.name}` : `#${row.station_id}`}
            </h3>
            {row.is_exceeded ? (
              <Tag tone={EXCEEDANCE_LEVEL_TONE[row.exceedance?.level] || 'danger'}>
                {row.exceedance?.level_label}
              </Tag>
            ) : (
              <Tag tone="success">达标</Tag>
            )}
          </div>

          <dl className="kv">
            <dt>监测时间</dt>
            <dd>{formatDateTime(row.measured_at)}</dd>
            <dt>监测因子</dt>
            <dd>{row.pollutant_label}</dd>
            <dt>数据周期</dt>
            <dd>{row.period_label}</dd>
            <dt>监测值</dt>
            <dd className="strong">
              {formatNumber(row.value)} {row.unit}
            </dd>
            <dt>适用限值</dt>
            <dd>{row.limit_value !== null ? `${formatNumber(row.limit_value)} ${row.unit || ''}` : '不设限值'}</dd>
            <dt>超标倍数</dt>
            <dd>{row.exceed_ratio !== null ? formatNumber(row.exceed_ratio) : '-'}</dd>
            <dt>数据来源</dt>
            <dd>{row.data_source_label}</dd>
            <dt>录入人</dt>
            <dd>{row.recorder || '-'}</dd>
            <dt>录入时间</dt>
            <dd>{formatDateTime(row.created_at)}</dd>
            <dt>最近更新</dt>
            <dd>{formatDateTime(row.updated_at)}</dd>
            <dt>备注</dt>
            <dd>{row.remark || '-'}</dd>
          </dl>

          <div className="card">
            <div className="card-header">
              <h3>判定结果</h3>
              {row.exceedance ? (
                <Tag tone={EXCEEDANCE_STATUS_TONE[row.exceedance.status]}>
                  {EXCEEDANCE_STATUS_LABELS[row.exceedance.status]}
                </Tag>
              ) : (
                <Tag tone="success">未超标, 无需标注</Tag>
              )}
            </div>
            {row.exceedance ? (
              <dl className="kv">
                <dt>超标记录编号</dt>
                <dd className="mono">#{row.exceedance.id}</dd>
                <dt>超标等级</dt>
                <dd>{row.exceedance.level_label}</dd>
                <dt>判定限值 / 倍数</dt>
                <dd>
                  {formatNumber(row.exceedance.limit_value)} · {formatNumber(row.exceedance.exceed_ratio)} 倍
                </dd>
                <dt>标注人</dt>
                <dd>{row.exceedance.annotator || '-'}</dd>
                <dt>标注时间</dt>
                <dd>{formatDateTime(row.exceedance.annotated_at)}</dd>
                <dt>标注说明</dt>
                <dd>{row.exceedance.note || '暂未标注'}</dd>
              </dl>
            ) : (
              <p className="hint" style={{ padding: '4px 2px' }}>
                该记录监测值未超过 GB 3095-2012 二级限值, 系统未生成超标记录。
              </p>
            )}
          </div>
        </div>
      ) : null}
    </Modal>
  )
}

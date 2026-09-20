import http, { toParams } from './client.js'

/**
 * 分层下钻: level 取值 area / station / pollutant / bucket / record。
 * params 中携带路径 (area, station_id, pollutant_code, bucket) 与基础筛选,
 * bucket / record 层额外携带 page / page_size。
 */
export const drilldown = (level, params) =>
  http.get(`/query/drilldown/${level}`, { params: toParams(params) })

export const fetchMeasurementDetail = (id) => http.get(`/measurements/${id}`)

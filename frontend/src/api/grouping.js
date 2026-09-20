import http, { toParams } from './client.js'

/** 统计分组逐层展开: level = area | station | pollutant | bucket | record */
export const fetchGrouping = (params) => http.get('/query/grouping', { params: toParams(params) })

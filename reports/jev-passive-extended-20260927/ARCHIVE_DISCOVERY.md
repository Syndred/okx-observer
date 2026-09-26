# 官方长期资金费档案可用性

2026-09-27。读取官方历史数据页公开前端代码，发现页面使用的公开下载接口，实际下载BTC的2026年3月资金费ZIP并解析成功，93行。未登录或读取私有账户。

- 页面：https://www.okx.com/en-sg/historical-data
- GET `/priapi/v5/broker/public/trade-data/instruments?instType=SWAP` 返回BTC-USDT等家族。
- POST `/priapi/v5/broker/public/trade-data/download-link` 参数：module=3、instType=SWAP、instQueryParam.instFamilyList=[BTC-USDT]、dateQuery.dateAggrType=monthly、begin/end为2026年3月起止毫秒时间字符串。
- 返回static.okx.com上的ZIP；CSV列为instrument_name、funding_rate、funding_time。文件哈希、来源和实际UTC范围见archive-probe.json。
- 不可直接将文件月份视为UTC月初/月末，必须按毫秒时间戳解析、切片、去重与核对结算覆盖。

本次只核验一个资金费文件，不代表全部币种、月份或更早K线已经齐全。官方网页/JS/接口响应/ZIP暂存于work/okx-*。未读取预留价格或策略标签。下一轮先冻结整个长期区间，再扩展下载，不能按表现选择日期。

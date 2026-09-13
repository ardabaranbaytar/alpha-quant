(function (root) {
  "use strict";
  // Generated historical backtest. Legacy demo schema; these are not fictional prices.
  const data = {
  "mode": "demo",
  "asOf": "2026-09-05T00:00:00Z",
  "currency": "USD",
  "trades": [
    {
      "id": "DEMO-001",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2022-04-01T13:30:00Z",
      "exitTime": "2022-04-06T13:30:00Z",
      "netPnl": 187.98,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-002",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2022-05-12T13:30:00Z",
      "exitTime": "2022-05-23T13:30:00Z",
      "netPnl": -431.04,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-003",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2022-05-13T13:30:00Z",
      "exitTime": "2022-05-31T13:30:00Z",
      "netPnl": -59.04,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-004",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2022-05-25T13:30:00Z",
      "exitTime": "2022-06-13T13:30:00Z",
      "netPnl": 65.25,
      "sector": "Financials"
    },
    {
      "id": "DEMO-005",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2022-08-15T13:30:00Z",
      "exitTime": "2022-08-22T13:30:00Z",
      "netPnl": 137.78,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-006",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2022-09-22T13:30:00Z",
      "exitTime": "2022-10-05T13:30:00Z",
      "netPnl": -38.94,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-007",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2022-10-03T13:30:00Z",
      "exitTime": "2022-10-14T13:30:00Z",
      "netPnl": 359.99,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-008",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2022-10-20T13:30:00Z",
      "exitTime": "2022-11-02T13:30:00Z",
      "netPnl": -132.04,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-009",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2022-11-04T13:30:00Z",
      "exitTime": "2022-11-18T14:30:00Z",
      "netPnl": 139.78,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-010",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2022-10-24T13:30:00Z",
      "exitTime": "2022-12-08T14:30:00Z",
      "netPnl": -1349.83,
      "sector": "Financials"
    },
    {
      "id": "DEMO-011",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2023-01-31T14:30:00Z",
      "exitTime": "2023-02-03T14:30:00Z",
      "netPnl": 272.97,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-012",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-02-24T14:30:00Z",
      "exitTime": "2023-03-02T14:30:00Z",
      "netPnl": 134.41,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-013",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2023-04-05T13:30:00Z",
      "exitTime": "2023-04-06T13:30:00Z",
      "netPnl": -59.73,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-014",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2023-04-17T13:30:00Z",
      "exitTime": "2023-04-27T13:30:00Z",
      "netPnl": -24.63,
      "sector": "Financials"
    },
    {
      "id": "DEMO-015",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2023-04-20T13:30:00Z",
      "exitTime": "2023-04-27T13:30:00Z",
      "netPnl": 90.08,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-016",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-05-04T13:30:00Z",
      "exitTime": "2023-05-05T13:30:00Z",
      "netPnl": 121.54,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-017",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-05-17T13:30:00Z",
      "exitTime": "2023-05-26T13:30:00Z",
      "netPnl": 424.81,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-018",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2023-05-26T13:30:00Z",
      "exitTime": "2023-06-01T13:30:00Z",
      "netPnl": 275.07,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-019",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2023-05-26T13:30:00Z",
      "exitTime": "2023-06-05T13:30:00Z",
      "netPnl": 262.53,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-020",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-06-22T13:30:00Z",
      "exitTime": "2023-06-29T13:30:00Z",
      "netPnl": 49.98,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-021",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2023-07-18T13:30:00Z",
      "exitTime": "2023-07-19T13:30:00Z",
      "netPnl": 133.75,
      "sector": "Financials"
    },
    {
      "id": "DEMO-022",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2023-07-21T13:30:00Z",
      "exitTime": "2023-08-01T13:30:00Z",
      "netPnl": 71.19,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-023",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2023-08-07T13:30:00Z",
      "exitTime": "2023-08-23T13:30:00Z",
      "netPnl": 130.32,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-024",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-08-25T13:30:00Z",
      "exitTime": "2023-09-05T13:30:00Z",
      "netPnl": 166.65,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-025",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2023-08-22T13:30:00Z",
      "exitTime": "2023-09-12T13:30:00Z",
      "netPnl": -159.53,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-026",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2023-10-25T13:30:00Z",
      "exitTime": "2023-10-26T13:30:00Z",
      "netPnl": 257.85,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-027",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-11-02T13:30:00Z",
      "exitTime": "2023-11-07T14:30:00Z",
      "netPnl": 72.35,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-028",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2023-10-30T13:30:00Z",
      "exitTime": "2023-11-13T14:30:00Z",
      "netPnl": 35.44,
      "sector": "Financials"
    },
    {
      "id": "DEMO-029",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2023-12-04T14:30:00Z",
      "exitTime": "2023-12-07T14:30:00Z",
      "netPnl": 76.3,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-030",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2023-12-15T14:30:00Z",
      "exitTime": "2023-12-19T14:30:00Z",
      "netPnl": 201.95,
      "sector": "Financials"
    },
    {
      "id": "DEMO-031",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2023-12-08T14:30:00Z",
      "exitTime": "2024-01-03T14:30:00Z",
      "netPnl": -184.15,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-032",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-01-17T14:30:00Z",
      "exitTime": "2024-01-19T14:30:00Z",
      "netPnl": 123.98,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-033",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2024-01-05T14:30:00Z",
      "exitTime": "2024-01-19T14:30:00Z",
      "netPnl": -398.13,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-034",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2024-01-22T14:30:00Z",
      "exitTime": "2024-02-02T14:30:00Z",
      "netPnl": -18.21,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-035",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2024-02-06T14:30:00Z",
      "exitTime": "2024-02-22T14:30:00Z",
      "netPnl": -464.83,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-036",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2024-02-23T14:30:00Z",
      "exitTime": "2024-02-28T14:30:00Z",
      "netPnl": 68.42,
      "sector": "Financials"
    },
    {
      "id": "DEMO-037",
      "pair": "AAPL / NVDA",
      "status": "CLOSED",
      "entryTime": "2024-03-06T14:30:00Z",
      "exitTime": "2024-03-11T13:30:00Z",
      "netPnl": 100.27,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-038",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-03-05T14:30:00Z",
      "exitTime": "2024-03-12T13:30:00Z",
      "netPnl": 65.24,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-039",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2024-03-20T13:30:00Z",
      "exitTime": "2024-03-28T13:30:00Z",
      "netPnl": -110.09,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-040",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2024-04-19T13:30:00Z",
      "exitTime": "2024-04-26T13:30:00Z",
      "netPnl": 104.26,
      "sector": "Financials"
    },
    {
      "id": "DEMO-041",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2024-04-18T13:30:00Z",
      "exitTime": "2024-05-03T13:30:00Z",
      "netPnl": 30.29,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-042",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2024-05-02T13:30:00Z",
      "exitTime": "2024-05-07T13:30:00Z",
      "netPnl": 79.32,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-043",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-05-06T13:30:00Z",
      "exitTime": "2024-05-24T13:30:00Z",
      "netPnl": -215.46,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-044",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-06-12T13:30:00Z",
      "exitTime": "2024-06-21T13:30:00Z",
      "netPnl": -121.66,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-045",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2024-06-06T13:30:00Z",
      "exitTime": "2024-06-21T13:30:00Z",
      "netPnl": -274.7,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-046",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2024-06-25T13:30:00Z",
      "exitTime": "2024-06-26T13:30:00Z",
      "netPnl": 183.11,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-047",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2024-07-18T13:30:00Z",
      "exitTime": "2024-07-24T13:30:00Z",
      "netPnl": 108.39,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-048",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2024-07-26T13:30:00Z",
      "exitTime": "2024-07-30T13:30:00Z",
      "netPnl": -34.89,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-049",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2024-07-31T13:30:00Z",
      "exitTime": "2024-08-14T13:30:00Z",
      "netPnl": 19.94,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-050",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2024-08-01T13:30:00Z",
      "exitTime": "2024-09-04T13:30:00Z",
      "netPnl": -277.95,
      "sector": "Financials"
    },
    {
      "id": "DEMO-051",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2024-09-11T13:30:00Z",
      "exitTime": "2024-09-12T13:30:00Z",
      "netPnl": 166.93,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-052",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-09-17T13:30:00Z",
      "exitTime": "2024-09-19T13:30:00Z",
      "netPnl": 255.48,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-053",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2024-09-11T13:30:00Z",
      "exitTime": "2024-09-24T13:30:00Z",
      "netPnl": 29.17,
      "sector": "Financials"
    },
    {
      "id": "DEMO-054",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-10-08T13:30:00Z",
      "exitTime": "2024-10-10T13:30:00Z",
      "netPnl": 111.95,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-055",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2024-11-07T14:30:00Z",
      "exitTime": "2024-11-08T14:30:00Z",
      "netPnl": 107.51,
      "sector": "Financials"
    },
    {
      "id": "DEMO-056",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2024-12-10T14:30:00Z",
      "exitTime": "2024-12-31T14:30:00Z",
      "netPnl": -385.58,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-057",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2025-01-23T14:30:00Z",
      "exitTime": "2025-02-21T14:30:00Z",
      "netPnl": -307.83,
      "sector": "Financials"
    },
    {
      "id": "DEMO-058",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2025-01-28T14:30:00Z",
      "exitTime": "2025-03-12T13:30:00Z",
      "netPnl": -789.47,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-059",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2025-03-13T13:30:00Z",
      "exitTime": "2025-03-28T13:30:00Z",
      "netPnl": -14.23,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-060",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2025-04-02T13:30:00Z",
      "exitTime": "2025-04-04T13:30:00Z",
      "netPnl": 252.0,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-061",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2025-04-10T13:30:00Z",
      "exitTime": "2025-04-16T13:30:00Z",
      "netPnl": 97.19,
      "sector": "Financials"
    },
    {
      "id": "DEMO-062",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2025-06-25T13:30:00Z",
      "exitTime": "2025-06-30T13:30:00Z",
      "netPnl": 179.98,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-063",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2025-07-18T13:30:00Z",
      "exitTime": "2025-07-24T13:30:00Z",
      "netPnl": 51.94,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-064",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2025-07-23T13:30:00Z",
      "exitTime": "2025-07-29T13:30:00Z",
      "netPnl": -10.53,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-065",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2025-07-30T13:30:00Z",
      "exitTime": "2025-08-05T13:30:00Z",
      "netPnl": 6.04,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-066",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2025-08-07T13:30:00Z",
      "exitTime": "2025-08-08T13:30:00Z",
      "netPnl": 162.96,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-067",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2025-08-11T13:30:00Z",
      "exitTime": "2025-09-11T13:30:00Z",
      "netPnl": -168.0,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-068",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2025-09-23T13:30:00Z",
      "exitTime": "2025-09-30T13:30:00Z",
      "netPnl": -0.42,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-069",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2025-10-09T13:30:00Z",
      "exitTime": "2025-10-10T13:30:00Z",
      "netPnl": 78.32,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-070",
      "pair": "JPM / BAC",
      "status": "CLOSED",
      "entryTime": "2025-10-14T13:30:00Z",
      "exitTime": "2025-10-15T13:30:00Z",
      "netPnl": 290.06,
      "sector": "Financials"
    },
    {
      "id": "DEMO-071",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2025-10-07T13:30:00Z",
      "exitTime": "2025-10-21T13:30:00Z",
      "netPnl": -310.95,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-072",
      "pair": "NVDA / AMD",
      "status": "CLOSED",
      "entryTime": "2025-10-16T13:30:00Z",
      "exitTime": "2025-10-23T13:30:00Z",
      "netPnl": 37.75,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-073",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2025-10-29T13:30:00Z",
      "exitTime": "2025-11-12T14:30:00Z",
      "netPnl": 85.24,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-074",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2025-12-24T14:30:00Z",
      "exitTime": "2026-01-13T14:30:00Z",
      "netPnl": -20.5,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-075",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2026-01-12T14:30:00Z",
      "exitTime": "2026-01-15T14:30:00Z",
      "netPnl": 28.59,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-076",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2026-01-29T14:30:00Z",
      "exitTime": "2026-02-02T14:30:00Z",
      "netPnl": 62.12,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-077",
      "pair": "AAPL / AMD",
      "status": "CLOSED",
      "entryTime": "2026-02-05T14:30:00Z",
      "exitTime": "2026-02-13T14:30:00Z",
      "netPnl": 434.09,
      "sector": "Technology / semiconductors"
    },
    {
      "id": "DEMO-078",
      "pair": "TRV / ALL",
      "status": "CLOSED",
      "entryTime": "2026-02-09T14:30:00Z",
      "exitTime": "2026-02-13T14:30:00Z",
      "netPnl": 64.19,
      "sector": "Insurance / specialized finance"
    },
    {
      "id": "DEMO-079",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2026-06-15T13:30:00Z",
      "exitTime": "2026-06-18T13:30:00Z",
      "netPnl": 63.01,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-080",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2026-07-08T13:30:00Z",
      "exitTime": "2026-07-10T13:30:00Z",
      "netPnl": 86.87,
      "sector": "Healthcare"
    },
    {
      "id": "DEMO-081",
      "pair": "JNJ / MRK",
      "status": "CLOSED",
      "entryTime": "2026-07-16T13:30:00Z",
      "exitTime": "2026-07-23T13:30:00Z",
      "netPnl": 24.14,
      "sector": "Healthcare"
    }
  ]
};
  const reportInfo = {
  "kind": "historical_backtest",
  "source": "Yahoo Finance via yfinance",
  "requestedStart": "2021-09-09",
  "requestedEndExclusive": "2026-09-05",
  "firstSession": "2021-09-09",
  "lastSession": "2026-09-04",
  "generatedAt": "2026-09-12T18:16:08Z",
  "execution": {
    "commission_bps": 10.0,
    "slippage_bps": 5.0,
    "annual_borrow_rate": 0.03
  },
  "positionSizing": {
    "method": "inverse_residual_volatility",
    "target_spread_risk": 100.0,
    "min_gross_notional": 10000.0,
    "max_gross_notional": 25000.0,
    "volatilityBars": 60,
    "notionalBasis": "Entry execution prices including slippage"
  },
  "trainingBars": 60,
  "model": "kalman",
  "portfolioConfig": {
    "initial_equity": 100000.0,
    "max_concurrent_pairs": 8,
    "max_ticker_fraction": 0.35,
    "risk_free_rate": 0.045,
    "max_sector_fraction": 0.4,
    "marginPolicy": "100% entry gross reserved; no short-proceeds reuse",
    "cashAccounting": "Open/close variation settlement; ACT/365 yield on time-held positive unreserved cash, credited at close; accrued borrow",
    "entryPriority": "Oldest signal, then alphabetical pair; exits first",
    "exposureBasis": "Gross legs without netting; post-entry marked equity",
    "insufficientCashPolicy": "Resize to available cash including entry costs; reject below minimum"
  },
  "riskManagement": {
    "max_holding_sessions": 45,
    "stop_z": 3.5
  },
  "entryFilters": {
    "max_hurst": 0.45,
    "hurst_window": 120,
    "early_profit_z": 0.5
  },
  "evaluationStart": "2021-09-09",
  "selection": "manual",
  "priceBasis": "Dividend- and split-adjusted daily OHLC; synthetic adjusted-share accounting",
  "executionTiming": "Signal at close, execution at next synchronized session open (09:30 America/New_York)"
};
  if (typeof module !== "undefined" && module.exports) module.exports = data;
  else { root.ALPHA_DEMO_DATA = data; root.ALPHA_REPORT_INFO = reportInfo; }
})(typeof globalThis !== "undefined" ? globalThis : this);

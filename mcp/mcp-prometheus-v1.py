from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field


PROMETHEUS_URL = os.getenv(
    'PROMETHEUS_URL',
    'http://localhost:9090',
)

mcp = FastMCP('prometheus')


class PrometheusClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')

    async def query(self, promql: str):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'{self.base_url}/api/v1/query',
                params={'query': promql},
            )
            r.raise_for_status()
            return r.json()

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str,
    ):
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f'{self.base_url}/api/v1/query_range',
                params={
                    'query': promql,
                    'start': start.timestamp(),
                    'end': end.timestamp(),
                    'step': step,
                },
            )
            r.raise_for_status()
            return r.json()

    async def labels(self):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'{self.base_url}/api/v1/labels'
            )
            r.raise_for_status()
            return r.json()

    async def label_values(self, label: str):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'{self.base_url}/api/v1/label/{label}/values'
            )
            r.raise_for_status()
            return r.json()

    async def metric_names(self):
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f'{self.base_url}/api/v1/label/__name__/values'
            )
            r.raise_for_status()
            return r.json()


prom = PrometheusClient(PROMETHEUS_URL)


class InstantQueryRequest(BaseModel):
    query: str = Field(
        description='PromQL query'
    )


class RangeQueryRequest(BaseModel):
    query: str
    hours: int = Field(
        default=1,
        ge=1,
        le=720,
        description='Lookback window'
    )
    step: str = Field(
        default='1m',
        description='Prometheus step'
    )


@mcp.tool()
async def run_query(query: str) -> dict[str, Any]:
    '''
    Execute an instant PromQL query.

    Example:
    rate(http_requests_total[5m])
    '''
    return await prom.query(query)


@mcp.tool()
async def run_range_query(
    query: str,
    hours: int = 1,
    step: str = '1m',
) -> dict[str, Any]:
    '''
    Execute a PromQL range query.

    Useful for charts and trends.
    '''
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    return await prom.query_range(
        promql=query,
        start=start,
        end=end,
        step=step,
    )


@mcp.tool()
async def list_metrics() -> list[str]:
    '''
    List all metric names known to Prometheus.
    '''
    result = await prom.metric_names()
    return result['data']


@mcp.tool()
async def list_labels() -> list[str]:
    '''
    List all available label names.
    '''
    result = await prom.labels()
    return result['data']


@mcp.tool()
async def get_label_values(
    label: str,
) -> list[str]:
    '''
    Get values for a label.

    Example:
    job
    instance
    namespace
    pod
    '''
    result = await prom.label_values(label)
    return result['data']


@mcp.resource('prometheus://status')
async def prometheus_status():
    return {
        'server': PROMETHEUS_URL,
        'connected': True,
    }


if __name__ == '__main__':
    mcp.run()

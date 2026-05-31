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

# extra tools

def extract_vector(response: dict[str, Any]) -> list[dict]:
    if response['status'] != 'success':
        raise RuntimeError(response)

    return response['data']['result']


def extract_scalar_value(item: dict) -> float:
    return float(item['value'][1])


def top_n(results: list[dict], n: int = 10):
    sorted_results = sorted(
        results,
        key=extract_scalar_value,
        reverse=True,
    )

    return sorted_results[:n]

@mcp.tool()
async def top_cpu_consumers(
    limit: int = 10,
) -> list[dict]:
    '''
    Top CPU-consuming Kubernetes pods.
    '''

    query = '''
    topk(
      50,
      sum by (namespace, pod)(
        rate(container_cpu_usage_seconds_total{
          container!='',
          pod!=''
        }[5m])
      )
    )
    '''

    response = await prom.query(query)

    results = top_n(
        extract_vector(response),
        limit,
    )

    return [
        {
            'namespace': r['metric'].get('namespace'),
            'pod': r['metric'].get('pod'),
            'cpu_cores': round(
                extract_scalar_value(r),
                4,
            ),
        }
        for r in results
    ]

@mcp.tool()
async def top_memory_consumers(
    limit: int = 10,
) -> list[dict]:
    '''
    Top memory-consuming pods.
    '''

    query = '''
    topk(
      50,
      sum by (namespace, pod)(
        container_memory_working_set_bytes{
          container!='',
          pod!=''
        }
      )
    )
    '''

    response = await prom.query(query)

    results = top_n(
        extract_vector(response),
        limit,
    )

    return [
        {
            'namespace': r['metric'].get('namespace'),
            'pod': r['metric'].get('pod'),
            'memory_mb': round(
                extract_scalar_value(r) / 1024 / 1024,
                1,
            ),
        }
        for r in results
    ]

@mcp.tool()
async def active_alerts() -> list[dict]:
    '''
    Return firing Prometheus alerts.
    '''

    query = 'ALERTS{alertstate='firing'}'

    response = await prom.query(query)

    results = extract_vector(response)

    alerts = []

    for r in results:
        metric = r['metric']

        alerts.append(
            {
                'alert': metric.get('alertname'),
                'severity': metric.get('severity'),
                'namespace': metric.get('namespace'),
                'instance': metric.get('instance'),
            }
        )

    return alerts

@mcp.tool()
async def pod_restarts(
    hours: int = 24,
    limit: int = 20,
) -> list[dict]:
    '''
    Pods with the most restarts.
    '''

    query = f'''
    topk(
      {limit},
      sum by(namespace,pod)(
        increase(
          kube_pod_container_status_restarts_total[{hours}h]
        )
      )
    )
    '''

    response = await prom.query(query)

    results = extract_vector(response)

    return [
        {
            'namespace': r['metric'].get('namespace'),
            'pod': r['metric'].get('pod'),
            'restarts': int(
                extract_scalar_value(r)
            ),
        }
        for r in results
    ]

@mcp.tool()
async def namespace_usage(
    namespace: str,
) -> dict:
    '''
    CPU and memory usage for a namespace.
    '''

    cpu_query = f'''
    sum(
      rate(
        container_cpu_usage_seconds_total{{
          namespace='{namespace}',
          container!=''
        }}[5m]
      )
    )
    '''

    mem_query = f'''
    sum(
      container_memory_working_set_bytes{{
        namespace='{namespace}',
        container!=''
      }}
    )
    '''

    cpu = await prom.query(cpu_query)
    mem = await prom.query(mem_query)

    cpu_value = (
        extract_scalar_value(
            extract_vector(cpu)[0]
        )
        if extract_vector(cpu)
        else 0
    )

    mem_value = (
        extract_scalar_value(
            extract_vector(mem)[0]
        )
        if extract_vector(mem)
        else 0
    )

    return {
        'namespace': namespace,
        'cpu_cores': round(cpu_value, 3),
        'memory_mb': round(
            mem_value / 1024 / 1024,
            1,
        ),
    }

@mcp.tool()
async def cluster_health() -> dict:
    '''
    High-level Kubernetes cluster health.
    '''

    node_query = '''
    sum(up{job=~'.*node.*'})
    '''

    pod_query = '''
    count(
      kube_pod_status_phase{
        phase='Running'
      }
    )
    '''

    alert_query = '''
    count(
      ALERTS{
        alertstate='firing'
      }
    )
    '''

    nodes = await prom.query(node_query)
    pods = await prom.query(pod_query)
    alerts = await prom.query(alert_query)

    return {
        'nodes_up': int(
            extract_scalar_value(
                extract_vector(nodes)[0]
            )
        ),
        'running_pods': int(
            extract_scalar_value(
                extract_vector(pods)[0]
            )
        ),
        'active_alerts': int(
            extract_scalar_value(
                extract_vector(alerts)[0]
            )
        ),
    }

@mcp.resource('prometheus://status')
async def prometheus_status():
    return {
        'server': PROMETHEUS_URL,
        'connected': True,
    }


if __name__ == '__main__':
    mcp.run()

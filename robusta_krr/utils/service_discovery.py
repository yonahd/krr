import logging
from typing import Optional

from cachetools import TTLCache
from kubernetes import client
from kubernetes.client import V1ServiceList, V1IngressList
from kubernetes.client.api_client import ApiClient
from kubernetes.client.models.v1_service import V1Service
from kubernetes.client.models.v1_ingress import V1Ingress
from kubernetes.config.config_exception import ConfigException

from robusta_krr.utils.configurable import Configurable


class ServiceDiscovery(Configurable):
    SERVICE_CACHE_TTL_SEC = 900
    cache: TTLCache = TTLCache(maxsize=1, ttl=SERVICE_CACHE_TTL_SEC)

    def find_service_url(self, label_selector: str, *, api_client: Optional[ApiClient] = None) -> Optional[str]:
        """
        Get the url of an in-cluster service with a specific label
        """
        # we do it this way because there is a weird issue with hikaru's ServiceList.listServiceForAllNamespaces()
        v1 = client.CoreV1Api(api_client=api_client)
        svc_list: V1ServiceList = v1.list_service_for_all_namespaces(label_selector=label_selector)
        if not svc_list.items:
            return None

        svc: V1Service = svc_list.items[0]
        name = svc.metadata.name
        namespace = svc.metadata.namespace
        port = svc.spec.ports[0].port

        if self.config.inside_cluster:
            return f"http://{name}.{namespace}.svc.cluster.local:{port}"

        elif api_client is not None:
            return f"{api_client.configuration.host}/api/v1/namespaces/{namespace}/services/{name}:{port}/proxy"

        return None

    def find_ingress_host(self, label_selector: str, *, api_client: Optional[ApiClient] = None) -> Optional[str]:
        """
        Discover the ingress host of the Prometheus if krr is not running in cluster
        """
        if self.config.inside_cluster:
            return None

        v1 = client.NetworkingV1Api(api_client=api_client)
        ingress_list: V1IngressList = v1.list_ingress_for_all_namespaces(label_selector=label_selector)
        if not ingress_list.items:
            return None

        ingress: V1Ingress = ingress_list.items[0]
        prometheus_host = ingress.spec.rules[0].host
        return f"http://{prometheus_host}"

    def find_url(self, selectors: list[str], *, api_client: Optional[ApiClient] = None) -> Optional[str]:
        """
        Try to autodiscover the url of an in-cluster service
        """
        cache_key = ",".join(selectors)
        cached_value = self.cache.get(cache_key)
        if cached_value:
            return cached_value

        for label_selector in selectors:
            logging.debug(f"Trying to find service with label selector {label_selector}")
            service_url = self.find_service_url(label_selector, api_client=api_client)
            if service_url:
                logging.debug(f"Found service with label selector {label_selector}")
                self.cache[cache_key] = service_url
                return service_url

            logging.debug(f"Trying to find ingress with label selector {label_selector}")
            self.find_ingress_host(label_selector, api_client=api_client)
            ingress_url = self.find_ingress_host(label_selector, api_client=api_client)
            if ingress_url:
                return ingress_url 

        return None

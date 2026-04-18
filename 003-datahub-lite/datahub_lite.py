"""
datahub_lite.py — 폐쇄망에서 동작하는 single-file DataHub 호환 메타데이터 카탈로그

원본 출처:
  DataHub (https://github.com/datahub-project/datahub) v1.4.x
  - 엔티티/aspect 모델, URN 컨벤션, MCP 레코드 포맷을 그대로 차용했습니다.
라이선스: Apache-2.0 (LICENSE 파일 참고)
생성: Code Conversion Agent

이 파일이 제공하는 것:
  1) DataHub 의 63개 엔티티 / 226개 aspect 메타데이터 모델을 로컬에서 그대로 사용
  2) 표준 라이브러리만으로 동작하는 in-memory + JSONL 카탈로그
  3) LLM tool calling 으로 호출 가능한 도구 함수 (OpenAI/Anthropic 호환 스키마)
  4) 실 DataHub 로 import 가능한 MCP(Metadata Change Proposal) JSONL export
  5) 보안 심사 통과 가능한 self-contained HTML 카탈로그

이 파일이 제공하지 *않는* 것:
  - DataHub 의 GraphQL/검색/Elasticsearch 같은 인프라 레이어
  - Pegasus 스키마 검증 (aspect 페이로드는 dict 로 느슨하게 저장)
  - 실시간 인제스션 파이프라인

Example:
  >>> cat = LocalCatalog("./catalog.jsonl")
  >>> urn = make_dataset_urn("hive", "fact_orders", env="PROD")
  >>> cat.upsert_dataset_properties(urn, description="주문 사실 테이블")
  >>> cat.add_tag(urn, "PII")
  >>> cat.add_upstream_lineage(urn, [make_dataset_urn("kafka","orders")])
  >>> cat.export_html("./catalog.html")
  >>> cat.export_mcps_jsonl("./mcps.jsonl")  # 실 DataHub 에 ingest 가능
"""

from __future__ import annotations

# ===== 1. Imports (표준 라이브러리만) =====
import datetime as _dt
import html as _html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
DATAHUB_COMPAT_VERSION = "1.4.x"  # 이 모듈이 호환을 목표로 한 DataHub 서버 버전대


# ===== 2. 엔티티/aspect 레지스트리 (DataHub 1.4.x snapshot, 임베디드) =====
# 단일 파일 원칙을 위해 외부 파일 의존 없이 인라인.
# 신규 엔티티/aspect 가 추가되면 registry/fetch_registry.py 로 재생성 후 이 블록 교체.

ENTITY_REGISTRY: dict[str, dict] = {
    'application': {'keyAspect': 'applicationKey', 'aspects': ('applicationKey', 'applicationProperties', 'domains', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'assertion': {'keyAspect': 'assertionKey', 'aspects': ('assertionActions', 'assertionInfo', 'assertionKey', 'assertionRunEvent', 'dataPlatformInstance', 'globalTags', 'status')},
    'businessAttribute': {'keyAspect': 'businessAttributeKey', 'aspects': ('businessAttributeInfo', 'businessAttributeKey', 'institutionalMemory', 'ownership', 'status')},
    'chart': {'keyAspect': 'chartKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'chartInfo', 'chartKey', 'chartQuery', 'chartUsageStatistics', 'container', 'dataPlatformInstance', 'deprecation', 'domains', 'editableChartProperties', 'embed', 'forms', 'globalTags', 'glossaryTerms', 'incidentsSummary', 'inputFields', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'container': {'keyAspect': 'containerKey', 'aspects': ('access', 'applications', 'browsePaths', 'browsePathsV2', 'container', 'containerKey', 'containerProperties', 'dataPlatformInstance', 'deprecation', 'domains', 'editableContainerProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'corpGroup': {'keyAspect': 'corpGroupKey', 'aspects': ('corpGroupEditableInfo', 'corpGroupInfo', 'corpGroupKey', 'forms', 'globalTags', 'origin', 'ownership', 'roleMembership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'corpuser': {'keyAspect': 'corpUserKey', 'aspects': ('corpUserCredentials', 'corpUserEditableInfo', 'corpUserInfo', 'corpUserKey', 'corpUserSettings', 'corpUserStatus', 'forms', 'globalTags', 'groupMembership', 'nativeGroupMembership', 'origin', 'roleMembership', 'slackUserInfo', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'dashboard': {'keyAspect': 'dashboardKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'container', 'dashboardInfo', 'dashboardKey', 'dashboardUsageStatistics', 'dataPlatformInstance', 'deprecation', 'domains', 'editableDashboardProperties', 'embed', 'forms', 'globalTags', 'glossaryTerms', 'incidentsSummary', 'inputFields', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'dataContract': {'keyAspect': 'dataContractKey', 'aspects': ('dataContractKey', 'dataContractProperties', 'dataContractStatus', 'status', 'structuredProperties')},
    'dataFlow': {'keyAspect': 'dataFlowKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'container', 'dataFlowInfo', 'dataFlowKey', 'dataPlatformInstance', 'deprecation', 'domains', 'editableDataFlowProperties', 'forms', 'globalTags', 'glossaryTerms', 'incidentsSummary', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults', 'versionInfo')},
    'dataHubAccessToken': {'keyAspect': 'dataHubAccessTokenKey', 'aspects': ('dataHubAccessTokenInfo', 'dataHubAccessTokenKey')},
    'dataHubAction': {'keyAspect': 'dataHubActionKey', 'aspects': ('dataHubActionKey',)},
    'dataHubConnection': {'keyAspect': 'dataHubConnectionKey', 'aspects': ('dataHubConnectionDetails', 'dataHubConnectionKey', 'dataPlatformInstance')},
    'dataHubExecutionRequest': {'keyAspect': 'dataHubExecutionRequestKey', 'aspects': ('dataHubExecutionRequestInput', 'dataHubExecutionRequestKey', 'dataHubExecutionRequestResult', 'dataHubExecutionRequestSignal')},
    'dataHubFile': {'keyAspect': 'dataHubFileKey', 'aspects': ('dataHubFileInfo', 'dataHubFileKey', 'status')},
    'dataHubIngestionSource': {'keyAspect': 'dataHubIngestionSourceKey', 'aspects': ('dataHubIngestionSourceInfo', 'dataHubIngestionSourceKey', 'ownership')},
    'dataHubOpenAPISchema': {'keyAspect': 'dataHubOpenAPISchemaKey', 'aspects': ('dataHubOpenAPISchemaKey', 'systemMetadata')},
    'dataHubPageModule': {'keyAspect': 'dataHubPageModuleKey', 'aspects': ('dataHubPageModuleKey', 'dataHubPageModuleProperties')},
    'dataHubPageTemplate': {'keyAspect': 'dataHubPageTemplateKey', 'aspects': ('dataHubPageTemplateKey', 'dataHubPageTemplateProperties')},
    'dataHubPersona': {'keyAspect': 'dataHubPersonaKey', 'aspects': ('dataHubPersonaInfo', 'dataHubPersonaKey')},
    'dataHubPolicy': {'keyAspect': 'dataHubPolicyKey', 'aspects': ('dataHubPolicyInfo', 'dataHubPolicyKey')},
    'dataHubRetention': {'keyAspect': 'dataHubRetentionKey', 'aspects': ('dataHubRetentionConfig', 'dataHubRetentionKey')},
    'dataHubRole': {'keyAspect': 'dataHubRoleKey', 'aspects': ('dataHubRoleInfo', 'dataHubRoleKey')},
    'dataHubSecret': {'keyAspect': 'dataHubSecretKey', 'aspects': ('dataHubSecretKey', 'dataHubSecretValue')},
    'dataHubStepState': {'keyAspect': 'dataHubStepStateKey', 'aspects': ('dataHubStepStateKey', 'dataHubStepStateProperties')},
    'dataHubUpgrade': {'keyAspect': 'dataHubUpgradeKey', 'aspects': ('dataHubUpgradeKey', 'dataHubUpgradeRequest', 'dataHubUpgradeResult')},
    'dataHubView': {'keyAspect': 'dataHubViewKey', 'aspects': ('dataHubViewInfo', 'dataHubViewKey')},
    'dataJob': {'keyAspect': 'dataJobKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'container', 'dataJobInfo', 'dataJobInputOutput', 'dataJobKey', 'dataPlatformInstance', 'dataTransformLogic', 'datahubIngestionCheckpoint', 'datahubIngestionRunSummary', 'deprecation', 'domains', 'editableDataJobProperties', 'forms', 'globalTags', 'glossaryTerms', 'incidentsSummary', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults', 'versionInfo')},
    'dataPlatform': {'keyAspect': 'dataPlatformKey', 'aspects': ('dataPlatformInfo', 'dataPlatformKey')},
    'dataPlatformInstance': {'keyAspect': 'dataPlatformInstanceKey', 'aspects': ('dataPlatformInstanceKey', 'dataPlatformInstanceProperties', 'deprecation', 'globalTags', 'icebergWarehouseInfo', 'institutionalMemory', 'ownership', 'status')},
    'dataProcess': {'keyAspect': 'dataProcessKey', 'aspects': ('dataProcessInfo', 'dataProcessKey', 'ownership', 'status', 'subTypes', 'testResults')},
    'dataProcessInstance': {'keyAspect': 'dataProcessInstanceKey', 'aspects': ('container', 'dataPlatformInstance', 'dataProcessInstanceInput', 'dataProcessInstanceKey', 'dataProcessInstanceOutput', 'dataProcessInstanceProperties', 'dataProcessInstanceRelationships', 'dataProcessInstanceRunEvent', 'mlTrainingRunProperties', 'status', 'subTypes', 'testResults')},
    'dataProduct': {'keyAspect': 'dataProductKey', 'aspects': ('applications', 'assetSettings', 'dataProductKey', 'dataProductProperties', 'domains', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'dataType': {'keyAspect': 'dataTypeKey', 'aspects': ('dataTypeInfo', 'dataTypeKey', 'institutionalMemory', 'status')},
    'dataset': {'keyAspect': 'datasetKey', 'aspects': ('access', 'applications', 'assetSettings', 'browsePaths', 'browsePathsV2', 'container', 'dataPlatformInstance', 'datasetDeprecation', 'datasetKey', 'datasetProfile', 'datasetProperties', 'datasetUpstreamLineage', 'datasetUsageStatistics', 'deprecation', 'domains', 'editableDatasetProperties', 'editableSchemaMetadata', 'embed', 'forms', 'globalTags', 'glossaryTerms', 'icebergCatalogInfo', 'incidentsSummary', 'institutionalMemory', 'logicalParent', 'operation', 'ownership', 'partitionsSummary', 'schemaMetadata', 'siblings', 'status', 'structuredProperties', 'subTypes', 'testResults', 'upstreamLineage', 'versionProperties', 'viewProperties')},
    'document': {'keyAspect': 'documentKey', 'aspects': ('browsePathsV2', 'dataPlatformInstance', 'documentInfo', 'documentKey', 'documentSettings', 'documentation', 'domains', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'ownership', 'semanticContent', 'status', 'structuredProperties', 'subTypes')},
    'domain': {'keyAspect': 'domainKey', 'aspects': ('assetSettings', 'displayProperties', 'domainKey', 'domainProperties', 'forms', 'institutionalMemory', 'ownership', 'structuredProperties', 'testResults')},
    'entityType': {'keyAspect': 'entityTypeKey', 'aspects': ('entityTypeInfo', 'entityTypeKey', 'institutionalMemory', 'status')},
    'erModelRelationship': {'keyAspect': 'erModelRelationshipKey', 'aspects': ('editableERModelRelationshipProperties', 'erModelRelationshipKey', 'erModelRelationshipProperties', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'ownership', 'status')},
    'form': {'keyAspect': 'formKey', 'aspects': ('dynamicFormAssignment', 'formInfo', 'formKey', 'ownership')},
    'globalSettings': {'keyAspect': 'globalSettingsKey', 'aspects': ('globalSettingsInfo', 'globalSettingsKey')},
    'glossaryNode': {'keyAspect': 'glossaryNodeKey', 'aspects': ('assetSettings', 'displayProperties', 'forms', 'glossaryNodeInfo', 'glossaryNodeKey', 'institutionalMemory', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'glossaryTerm': {'keyAspect': 'glossaryTermKey', 'aspects': ('applications', 'assetSettings', 'browsePaths', 'deprecation', 'domains', 'forms', 'glossaryRelatedTerms', 'glossaryTermInfo', 'glossaryTermKey', 'institutionalMemory', 'ownership', 'schemaMetadata', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'incident': {'keyAspect': 'incidentKey', 'aspects': ('globalTags', 'incidentInfo', 'incidentKey')},
    'inviteToken': {'keyAspect': 'inviteTokenKey', 'aspects': ('inviteToken', 'inviteTokenKey')},
    'mlFeature': {'keyAspect': 'mlFeatureKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'dataPlatformInstance', 'deprecation', 'domains', 'editableMlFeatureProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'mlFeatureKey', 'mlFeatureProperties', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'mlFeatureTable': {'keyAspect': 'mlFeatureTableKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'dataPlatformInstance', 'deprecation', 'domains', 'editableMlFeatureTableProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'mlFeatureTableKey', 'mlFeatureTableProperties', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'mlModel': {'keyAspect': 'mlModelKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'container', 'cost', 'dataPlatformInstance', 'deprecation', 'domains', 'editableMlModelProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'intendedUse', 'mlModelCaveatsAndRecommendations', 'mlModelEthicalConsiderations', 'mlModelEvaluationData', 'mlModelFactorPrompts', 'mlModelKey', 'mlModelMetrics', 'mlModelProperties', 'mlModelQuantitativeAnalyses', 'mlModelTrainingData', 'ownership', 'sourceCode', 'status', 'structuredProperties', 'subTypes', 'testResults', 'versionProperties')},
    'mlModelDeployment': {'keyAspect': 'mlModelDeploymentKey', 'aspects': ('container', 'dataPlatformInstance', 'deprecation', 'globalTags', 'mlModelDeploymentKey', 'mlModelDeploymentProperties', 'ownership', 'status', 'testResults')},
    'mlModelGroup': {'keyAspect': 'mlModelGroupKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'container', 'dataPlatformInstance', 'deprecation', 'domains', 'editableMlModelGroupProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'mlModelGroupKey', 'mlModelGroupProperties', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'mlPrimaryKey': {'keyAspect': 'mlPrimaryKeyKey', 'aspects': ('applications', 'dataPlatformInstance', 'deprecation', 'domains', 'editableMlPrimaryKeyProperties', 'forms', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'mlPrimaryKeyKey', 'mlPrimaryKeyProperties', 'ownership', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'notebook': {'keyAspect': 'notebookKey', 'aspects': ('applications', 'browsePaths', 'browsePathsV2', 'dataPlatformInstance', 'domains', 'editableNotebookProperties', 'globalTags', 'glossaryTerms', 'institutionalMemory', 'notebookContent', 'notebookInfo', 'notebookKey', 'ownership', 'status', 'subTypes', 'testResults')},
    'ownershipType': {'keyAspect': 'ownershipTypeKey', 'aspects': ('ownershipTypeInfo', 'ownershipTypeKey', 'status')},
    'platformResource': {'keyAspect': 'platformResourceKey', 'aspects': ('dataPlatformInstance', 'platformResourceInfo', 'platformResourceKey', 'status')},
    'post': {'keyAspect': 'postKey', 'aspects': ('postInfo', 'postKey', 'subTypes')},
    'query': {'keyAspect': 'queryKey', 'aspects': ('dataPlatformInstance', 'queryKey', 'queryProperties', 'querySubjects', 'queryUsageStatistics', 'status', 'subTypes')},
    'role': {'keyAspect': 'roleKey', 'aspects': ('actors', 'roleKey', 'roleProperties')},
    'schemaField': {'keyAspect': 'schemaFieldKey', 'aspects': ('businessAttributes', 'deprecation', 'documentation', 'forms', 'globalTags', 'glossaryTerms', 'logicalParent', 'schemaFieldAliases', 'schemaFieldKey', 'schemafieldInfo', 'status', 'structuredProperties', 'subTypes', 'testResults')},
    'structuredProperty': {'keyAspect': 'structuredPropertyKey', 'aspects': ('institutionalMemory', 'propertyDefinition', 'status', 'structuredPropertyKey', 'structuredPropertySettings')},
    'tag': {'keyAspect': 'tagKey', 'aspects': ('deprecation', 'ownership', 'status', 'tagKey', 'tagProperties', 'testResults')},
    'telemetry': {'keyAspect': 'telemetryKey', 'aspects': ('telemetryClientId', 'telemetryKey')},
    'test': {'keyAspect': 'testKey', 'aspects': ('testInfo', 'testKey')},
    'versionSet': {'keyAspect': 'versionSetKey', 'aspects': ('versionSetKey', 'versionSetProperties')},
}

TIMESERIES_ASPECTS: frozenset[str] = frozenset({
    'assertionRunEvent', 'chartUsageStatistics', 'dashboardUsageStatistics',
    'dataProcessInstanceRunEvent', 'datahubIngestionCheckpoint',
    'datahubIngestionRunSummary', 'datasetProfile', 'datasetUsageStatistics',
    'operation', 'queryUsageStatistics',
})


# ===== 3. URN 유틸리티 =====
# DataHub URN 형식: urn:li:<entityType>:<keyTuple|simpleId>
#   예) urn:li:dataset:(urn:li:dataPlatform:hive,fact_orders,PROD)
#   예) urn:li:tag:PII
#   예) urn:li:corpuser:alice

URN_RE = re.compile(r"^urn:li:([a-zA-Z]+):(.+)$")


def parse_urn(urn: str) -> tuple[str, str]:
    """URN 을 (entityType, keyPart) 로 분해.

    keyPart 가 괄호로 감싸져 있으면 내부 컴마 분리는 호출자가 처리.
    """
    m = URN_RE.match(urn)
    if not m:
        raise ValueError(f"잘못된 URN 형식입니다: {urn!r}")
    entity_type, key_part = m.group(1), m.group(2)
    if entity_type not in ENTITY_REGISTRY:
        # DataHub 가 새 엔티티를 추가했을 수 있으므로 경고만, 통과시킴
        logger.debug("등록되지 않은 entityType: %s", entity_type)
    return entity_type, key_part


def split_compound_key(key_part: str) -> list[str]:
    """`(a,b,c)` 형식의 복합 키를 [a, b, c] 로 분해 (중첩 괄호 지원)."""
    if not (key_part.startswith("(") and key_part.endswith(")")):
        return [key_part]
    inner = key_part[1:-1]
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in inner:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def make_data_platform_urn(platform: str) -> str:
    """`urn:li:dataPlatform:<platform>` (예: hive, kafka, snowflake)"""
    return f"urn:li:dataPlatform:{platform}"


def make_dataset_urn(platform: str, name: str, env: str = "PROD") -> str:
    """Dataset URN 생성. platform 은 짧은 식별자 (예: hive, kafka)."""
    p = make_data_platform_urn(platform) if not platform.startswith("urn:") else platform
    return f"urn:li:dataset:({p},{name},{env})"


def make_chart_urn(tool: str, chart_id: str) -> str:
    return f"urn:li:chart:({tool},{chart_id})"


def make_dashboard_urn(tool: str, dashboard_id: str) -> str:
    return f"urn:li:dashboard:({tool},{dashboard_id})"


def make_data_flow_urn(orchestrator: str, flow_id: str, cluster: str = "PROD") -> str:
    return f"urn:li:dataFlow:({orchestrator},{flow_id},{cluster})"


def make_data_job_urn(orchestrator: str, flow_id: str, job_id: str, cluster: str = "PROD") -> str:
    flow = make_data_flow_urn(orchestrator, flow_id, cluster)
    return f"urn:li:dataJob:({flow},{job_id})"


def make_tag_urn(tag: str) -> str:
    return f"urn:li:tag:{tag}"


def make_glossary_term_urn(term: str) -> str:
    return f"urn:li:glossaryTerm:{term}"


def make_corp_user_urn(username: str) -> str:
    return f"urn:li:corpuser:{username}"


def make_corp_group_urn(group: str) -> str:
    return f"urn:li:corpGroup:{group}"


def make_domain_urn(domain_id: str) -> str:
    return f"urn:li:domain:{domain_id}"


def make_container_urn(container_id: str) -> str:
    return f"urn:li:container:{container_id}"


def make_schema_field_urn(parent_urn: str, field_path: str) -> str:
    return f"urn:li:schemaField:({parent_urn},{field_path})"


# ===== 4. MCP (Metadata Change Proposal) 데이터 모델 =====
# DataHub 의 MCP 와 호환되는 최소 스펙. 실 DataHub ingest 시 이대로 emit 가능.

@dataclass
class AspectChange:
    """단일 (entity, aspect) 업서트를 나타내는 변경 레코드. DataHub MCP 와 호환."""
    entityUrn: str
    entityType: str
    aspectName: str
    aspect: dict
    changeType: str = "UPSERT"  # UPSERT | CREATE | UPDATE | DELETE | PATCH
    systemMetadata: dict = field(default_factory=dict)

    def to_mcp_dict(self) -> dict:
        """DataHub Python SDK 가 받아들이는 MCP JSON 모양으로 직렬화.

        주의: 내부 aspect 값은 반드시 `ensure_ascii=True` 로 직렬화해야 합니다.
        DataHub Pegasus 서버는 GenericAspect.value 를 bytes 로 다루는데,
        UTF-8 multi-byte (예: 한글) 가 그대로 들어가면 1-byte-per-char 검증에 실패합니다.
        """
        return {
            "entityType": self.entityType,
            "entityUrn": self.entityUrn,
            "changeType": self.changeType,
            "aspectName": self.aspectName,
            "aspect": {
                "value": json.dumps(self.aspect, ensure_ascii=True),
                "contentType": "application/json",
            },
            "systemMetadata": self.systemMetadata or {
                "lastObserved": int(time.time() * 1000),
                "runId": "datahub-lite",
            },
        }

    def to_jsonl_dict(self) -> dict:
        """로컬 저장용 JSONL 한 줄 표현. aspect 를 그대로 보관해 가독성 유지."""
        return {
            "entityType": self.entityType,
            "entityUrn": self.entityUrn,
            "aspectName": self.aspectName,
            "aspect": self.aspect,
            "changeType": self.changeType,
            "systemMetadata": self.systemMetadata,
        }

    @classmethod
    def from_jsonl_dict(cls, d: dict) -> "AspectChange":
        return cls(
            entityType=d["entityType"],
            entityUrn=d["entityUrn"],
            aspectName=d["aspectName"],
            aspect=d.get("aspect", {}),
            changeType=d.get("changeType", "UPSERT"),
            systemMetadata=d.get("systemMetadata", {}),
        )


# ===== 5. LocalCatalog: 인-메모리 카탈로그 + JSONL 영속화 =====

class LocalCatalog:
    """DataHub 호환 메타데이터를 로컬에서 관리하는 카탈로그.

    저장 모델
    --------
    - 메모리: dict[entity_urn][aspect_name] = aspect_dict
    - 디스크: JSONL append-only 로그 (한 줄 = 한 AspectChange)

    JSONL 은 순수 텍스트라 보안 심사 통과가 용이하며, 그대로 DataHub 로
    import 도 가능합니다 (export_mcps_jsonl 참고).
    """

    def __init__(self, path: str | os.PathLike | None = None, *, autosave: bool = True):
        self.path: Optional[Path] = Path(path) if path else None
        self.autosave = autosave
        # entity_urn -> aspect_name -> aspect dict
        self._aspects: dict[str, dict[str, dict]] = {}
        # entity_urn -> entity_type (캐시)
        self._types: dict[str, str] = {}
        # 변경 로그 (현재 세션 내 추가분만 기억; 디스크에는 즉시 기록)
        self._changes: list[AspectChange] = []
        if self.path and self.path.exists():
            self._load()

    # ---------- 영속화 ----------

    def _load(self) -> None:
        assert self.path is not None
        with self.path.open("r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as e:
                    logger.warning("JSONL %s:%d parse 실패: %s", self.path, ln, e)
                    continue
                self._apply(AspectChange.from_jsonl_dict(d), persist=False)
        logger.info("loaded %d entities from %s", len(self._aspects), self.path)

    def save(self, path: str | os.PathLike | None = None) -> Path:
        """전체 카탈로그를 JSONL 로 저장 (스냅샷). 기존 로그를 덮어씀."""
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("저장 경로가 지정되지 않았습니다.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            for urn in sorted(self._aspects):
                etype = self._types.get(urn) or parse_urn(urn)[0]
                for aspect_name in sorted(self._aspects[urn]):
                    rec = AspectChange(
                        entityUrn=urn,
                        entityType=etype,
                        aspectName=aspect_name,
                        aspect=self._aspects[urn][aspect_name],
                    ).to_jsonl_dict()
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return target

    def _append_log(self, change: AspectChange) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(change.to_jsonl_dict(), ensure_ascii=False) + "\n")

    # ---------- 핵심 read API ----------

    def get(self, entity_urn: str, aspect_name: str) -> Optional[dict]:
        """엔티티의 특정 aspect 를 반환. 없으면 None."""
        return self._aspects.get(entity_urn, {}).get(aspect_name)

    def get_entity(self, entity_urn: str) -> dict[str, dict]:
        """엔티티의 모든 aspect 를 dict 로 반환."""
        return dict(self._aspects.get(entity_urn, {}))

    def exists(self, entity_urn: str) -> bool:
        return entity_urn in self._aspects

    def list_entities(self, entity_type: str | None = None) -> list[str]:
        """등록된 엔티티 URN 목록. entity_type 으로 필터링 가능."""
        urns = list(self._aspects.keys())
        if entity_type:
            urns = [u for u in urns if self._types.get(u) == entity_type]
        return sorted(urns)

    def stats(self) -> dict[str, int]:
        """엔티티 타입별 개수 요약."""
        out: dict[str, int] = {}
        for u in self._aspects:
            t = self._types.get(u, "unknown")
            out[t] = out.get(t, 0) + 1
        return out

    def search(
        self,
        query: str,
        entity_type: str | None = None,
        tags: Sequence[str] = (),
        limit: int = 50,
    ) -> list[dict]:
        """간단한 substring + 태그 필터 검색.

        검색 대상 필드:
          - URN 자체
          - <entity>Properties 의 name/description
          - editable* aspect 의 description
        """
        q = query.lower().strip()
        tag_set = {make_tag_urn(t) if not t.startswith("urn:") else t for t in tags}
        results: list[tuple[float, dict]] = []
        for urn, aspects in self._aspects.items():
            etype = self._types.get(urn, "")
            if entity_type and etype != entity_type:
                continue
            if tag_set:
                attached = {
                    t.get("tag")
                    for t in (aspects.get("globalTags") or {}).get("tags", [])
                    if isinstance(t, dict)
                }
                if not tag_set.issubset(attached):
                    continue

            score = 0.0
            haystack = [urn]
            for asp_name, payload in aspects.items():
                if asp_name.endswith("Properties") or asp_name.startswith("editable"):
                    name = (payload or {}).get("name") or (payload or {}).get("displayName")
                    desc = (payload or {}).get("description")
                    if name:
                        haystack.append(str(name))
                    if desc:
                        haystack.append(str(desc))
            text = " ".join(haystack).lower()
            if q and q not in text:
                continue
            score += text.count(q)
            results.append((score, {
                "urn": urn,
                "entityType": etype,
                "displayName": _best_display_name(aspects, urn),
                "score": score,
            }))
        results.sort(key=lambda r: (-r[0], r[1]["urn"]))
        return [r[1] for r in results[:limit]]

    # ---------- 핵심 write API ----------

    def upsert(self, entity_urn: str, aspect_name: str, aspect: dict) -> AspectChange:
        """범용 upsert. 모든 편의 메서드의 기반."""
        etype, _ = parse_urn(entity_urn)
        if etype in ENTITY_REGISTRY:
            allowed = ENTITY_REGISTRY[etype]["aspects"]
            if aspect_name not in allowed:
                # DataHub 가 다음 버전에 추가했거나 사용자가 커스텀 aspect 를 쓰는 경우
                logger.warning(
                    "%s 엔티티는 표준 레지스트리에 %r aspect 가 없습니다 (저장은 진행).",
                    etype, aspect_name,
                )
        change = AspectChange(
            entityUrn=entity_urn,
            entityType=etype,
            aspectName=aspect_name,
            aspect=aspect,
        )
        self._apply(change, persist=self.autosave)
        return change

    def _apply(self, change: AspectChange, persist: bool) -> None:
        urn = change.entityUrn
        if urn not in self._aspects:
            self._aspects[urn] = {}
            self._types[urn] = change.entityType
        if change.changeType == "DELETE":
            self._aspects[urn].pop(change.aspectName, None)
            if not self._aspects[urn]:
                self._aspects.pop(urn, None)
                self._types.pop(urn, None)
        else:
            self._aspects[urn][change.aspectName] = change.aspect
        self._changes.append(change)
        if persist:
            self._append_log(change)

    def delete_aspect(self, entity_urn: str, aspect_name: str) -> None:
        change = AspectChange(
            entityUrn=entity_urn,
            entityType=self._types.get(entity_urn) or parse_urn(entity_urn)[0],
            aspectName=aspect_name,
            aspect={},
            changeType="DELETE",
        )
        self._apply(change, persist=self.autosave)

    def soft_delete(self, entity_urn: str) -> AspectChange:
        """status.removed = True 로 표시 (DataHub 의 soft delete 컨벤션)."""
        return self.upsert(entity_urn, "status", {"removed": True})

    # ---------- 편의 메서드 (자주 쓰는 aspect) ----------

    def upsert_dataset_properties(
        self,
        dataset_urn: str,
        *,
        description: str | None = None,
        custom_properties: dict | None = None,
        external_url: str | None = None,
        name: str | None = None,
    ) -> AspectChange:
        payload: dict = {}
        if description is not None:
            payload["description"] = description
        if custom_properties is not None:
            payload["customProperties"] = custom_properties
        if external_url is not None:
            payload["externalUrl"] = external_url
        if name is not None:
            payload["name"] = name
        return self.upsert(dataset_urn, "datasetProperties", payload)

    def upsert_schema_metadata(
        self,
        dataset_urn: str,
        fields: list[dict],
        *,
        platform: str | None = None,
        schema_name: str = "schema",
        version: int = 0,
    ) -> AspectChange:
        """SchemaMetadata aspect 를 간편하게 입력.

        fields 예시:
          [{"fieldPath": "id", "type": "long", "description": "주문 ID", "nullable": False},
           {"fieldPath": "amount", "type": "double", "description": "금액"}]
        """
        if platform is None:
            etype, key = parse_urn(dataset_urn)
            parts = split_compound_key(key)
            platform = parts[0] if parts else ""
        norm_fields = []
        for f in fields:
            t = f.get("type", "string")
            norm_fields.append({
                "fieldPath": f["fieldPath"],
                "nullable": f.get("nullable", True),
                "description": f.get("description", ""),
                "type": {"type": {f"com.linkedin.schema.{_pegasus_type(t)}": {}}},
                "nativeDataType": f.get("nativeDataType", t),
                "recursive": False,
            })
        payload = {
            "schemaName": schema_name,
            "platform": platform if platform.startswith("urn:") else make_data_platform_urn(platform),
            "version": version,
            "hash": "",
            # SchemaMetadata.platformSchema 는 union — Schemaless 가 가장 범용/안전한 기본값.
            "platformSchema": {"com.linkedin.schema.Schemaless": {}},
            "fields": norm_fields,
        }
        return self.upsert(dataset_urn, "schemaMetadata", payload)

    def add_tag(self, entity_urn: str, tag: str) -> AspectChange:
        """globalTags aspect 에 태그를 추가 (중복 제거)."""
        tag_urn = make_tag_urn(tag) if not tag.startswith("urn:") else tag
        existing = self.get(entity_urn, "globalTags") or {"tags": []}
        tags = list(existing.get("tags", []))
        if not any(t.get("tag") == tag_urn for t in tags):
            tags.append({"tag": tag_urn})
        # tag 엔티티 자체도 자동 등록
        if not self.exists(tag_urn):
            self.upsert(tag_urn, "tagKey", {"name": tag_urn.split(":", 3)[-1]})
        return self.upsert(entity_urn, "globalTags", {"tags": tags})

    def add_glossary_term(self, entity_urn: str, term: str) -> AspectChange:
        term_urn = make_glossary_term_urn(term) if not term.startswith("urn:") else term
        existing = self.get(entity_urn, "glossaryTerms") or {"terms": [], "auditStamp": _audit_now()}
        terms = list(existing.get("terms", []))
        if not any(t.get("urn") == term_urn for t in terms):
            terms.append({"urn": term_urn})
        return self.upsert(entity_urn, "glossaryTerms", {"terms": terms, "auditStamp": _audit_now()})

    def add_owner(
        self,
        entity_urn: str,
        owner: str,
        *,
        owner_type: str = "DATAOWNER",
    ) -> AspectChange:
        """소유자 추가. owner 는 corpuser/corpGroup URN 또는 단순 username."""
        owner_urn = owner if owner.startswith("urn:") else make_corp_user_urn(owner)
        existing = self.get(entity_urn, "ownership") or {"owners": [], "lastModified": _audit_now()}
        owners = list(existing.get("owners", []))
        if not any(o.get("owner") == owner_urn for o in owners):
            # source 필드는 OwnershipSource record 가 필요해 None 으로 두면 422 발생.
            # 단순화를 위해 아예 생략 (DataHub 가 자체적으로 audit info 채움).
            owners.append({"owner": owner_urn, "type": owner_type})
        return self.upsert(entity_urn, "ownership", {"owners": owners, "lastModified": _audit_now()})

    def set_domain(self, entity_urn: str, domain: str) -> AspectChange:
        domain_urn = domain if domain.startswith("urn:") else make_domain_urn(domain)
        if not self.exists(domain_urn):
            self.upsert(domain_urn, "domainProperties", {"name": domain_urn.split(":", 3)[-1]})
        return self.upsert(entity_urn, "domains", {"domains": [domain_urn]})

    def add_upstream_lineage(
        self,
        downstream_urn: str,
        upstream_urns: Sequence[str],
        *,
        lineage_type: str = "TRANSFORMED",
    ) -> AspectChange:
        """upstreamLineage aspect 에 상류 노드들을 누적 추가 (중복 제거)."""
        existing = self.get(downstream_urn, "upstreamLineage") or {"upstreams": []}
        upstreams = list(existing.get("upstreams", []))
        seen = {u.get("dataset") for u in upstreams if isinstance(u, dict)}
        for up in upstream_urns:
            if up in seen:
                continue
            upstreams.append({
                "dataset": up,
                "type": lineage_type,
                "auditStamp": _audit_now(),
            })
            seen.add(up)
        return self.upsert(downstream_urn, "upstreamLineage", {"upstreams": upstreams})

    def get_upstream_urns(self, entity_urn: str) -> list[str]:
        a = self.get(entity_urn, "upstreamLineage") or {}
        return [u.get("dataset") for u in a.get("upstreams", []) if isinstance(u, dict)]

    def get_downstream_urns(self, entity_urn: str) -> list[str]:
        """역방향 검색 (모든 엔티티 스캔). 대량 카탈로그에서는 비싸므로 주의."""
        out = []
        for urn, aspects in self._aspects.items():
            ups = aspects.get("upstreamLineage", {}).get("upstreams", [])
            if any(u.get("dataset") == entity_urn for u in ups if isinstance(u, dict)):
                out.append(urn)
        return sorted(out)

    # ---------- export ----------

    def export_mcps_jsonl(self, path: str | os.PathLike) -> Path:
        """현재 카탈로그 전체를 DataHub-호환 MCP JSONL 로 export.

        실 DataHub 에 import 하려면:
          datahub ingest -c mcps_recipe.yml      # acryl-datahub CLI 사용
        또는 examples/export_to_datahub.py 참고.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for urn in sorted(self._aspects):
                etype = self._types.get(urn) or parse_urn(urn)[0]
                for aspect_name in sorted(self._aspects[urn]):
                    change = AspectChange(
                        entityUrn=urn,
                        entityType=etype,
                        aspectName=aspect_name,
                        aspect=self._aspects[urn][aspect_name],
                    )
                    f.write(json.dumps(change.to_mcp_dict(), ensure_ascii=False) + "\n")
        return out

    def iter_mcps(self) -> Iterator[dict]:
        for urn in sorted(self._aspects):
            etype = self._types.get(urn) or parse_urn(urn)[0]
            for aspect_name in sorted(self._aspects[urn]):
                yield AspectChange(
                    entityUrn=urn,
                    entityType=etype,
                    aspectName=aspect_name,
                    aspect=self._aspects[urn][aspect_name],
                ).to_mcp_dict()

    def export_html(self, path: str | os.PathLike, *, title: str = "DataHub Lite Catalog") -> Path:
        """모든 데이터를 임베드한 self-contained HTML 카탈로그 생성.

        보안 심사를 통과시키기 위해 외부 fetch/CDN/이미지 src 를 일절 사용하지 않습니다.
        모든 데이터·CSS·JS 가 한 파일에 인라인됩니다.
        """
        return _render_html_catalog(self, Path(path), title=title)


# ===== 6. 헬퍼 =====

def _audit_now(actor: str = "urn:li:corpuser:datahub-lite") -> dict:
    return {"time": int(time.time() * 1000), "actor": actor}


_PEGASUS_TYPE_MAP = {
    "string": "StringType",
    "str": "StringType",
    "int": "NumberType",
    "long": "NumberType",
    "integer": "NumberType",
    "double": "NumberType",
    "float": "NumberType",
    "decimal": "NumberType",
    "bool": "BooleanType",
    "boolean": "BooleanType",
    "date": "DateType",
    "timestamp": "TimeType",
    "datetime": "TimeType",
    "bytes": "BytesType",
    "binary": "BytesType",
    "array": "ArrayType",
    "map": "MapType",
    "struct": "RecordType",
    "record": "RecordType",
}


def _pegasus_type(name: str) -> str:
    return _PEGASUS_TYPE_MAP.get(name.lower(), "StringType")


def _best_display_name(aspects: dict, urn: str) -> str:
    """검색 결과에서 보여줄 사람-친화 이름."""
    for asp_name in ("datasetProperties", "chartInfo", "dashboardInfo", "dataJobInfo",
                     "dataFlowInfo", "containerProperties", "domainProperties",
                     "tagProperties", "glossaryTermInfo", "applicationProperties",
                     "corpUserInfo", "corpGroupInfo", "mlModelProperties"):
        a = aspects.get(asp_name) or {}
        for key in ("name", "displayName", "title"):
            if a.get(key):
                return a[key]
    # URN 마지막 토큰
    parts = split_compound_key(parse_urn(urn)[1])
    return parts[-2] if len(parts) >= 2 else parts[-1] if parts else urn


# ===== 7. LLM Tool Calling 인터페이스 =====
# OpenAI / Anthropic / 로컬 LLM 모두에서 쓰기 좋은 JSON Schema 형태로 노출.
# 함수 시그니처와 1:1 매핑되도록 단순화함.

TOOLS: list[dict] = [
    {
        "name": "search_entities",
        "description": "카탈로그에서 엔티티를 검색합니다. 이름·설명·URN 의 부분 일치를 지원하며 entity_type/tags 로 필터링할 수 있습니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드 (빈 문자열이면 모든 엔티티)"},
                "entity_type": {"type": "string", "description": "예: dataset, chart, dashboard, dataJob, mlModel. 생략 가능."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "필수 태그 목록 (모두 매칭)."},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_entity",
        "description": "특정 엔티티의 모든 aspect 를 반환합니다.",
        "parameters": {
            "type": "object",
            "properties": {"entity_urn": {"type": "string"}},
            "required": ["entity_urn"],
        },
    },
    {
        "name": "get_aspect",
        "description": "특정 엔티티의 단일 aspect 만 반환합니다 (예: datasetProperties, schemaMetadata).",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_urn": {"type": "string"},
                "aspect_name": {"type": "string"},
            },
            "required": ["entity_urn", "aspect_name"],
        },
    },
    {
        "name": "upsert_aspect",
        "description": "임의의 aspect 페이로드(dict)를 엔티티에 upsert 합니다. 고급 사용자용.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_urn": {"type": "string"},
                "aspect_name": {"type": "string"},
                "aspect": {"type": "object", "description": "DataHub aspect 페이로드 (JSON 객체)"},
            },
            "required": ["entity_urn", "aspect_name", "aspect"],
        },
    },
    {
        "name": "register_dataset",
        "description": "Dataset 엔티티를 한 번에 등록합니다 (URN 생성 + datasetProperties + schema).",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "예: hive, kafka, snowflake, mysql"},
                "name": {"type": "string", "description": "데이터셋의 정규화된 이름 (예: db.schema.table)"},
                "env": {"type": "string", "default": "PROD"},
                "description": {"type": "string"},
                "fields": {
                    "type": "array",
                    "description": "[{fieldPath, type, description, nullable}] 형태의 스키마 컬럼 목록.",
                    "items": {"type": "object"},
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "owners": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["platform", "name"],
        },
    },
    {
        "name": "tag_entity",
        "description": "엔티티에 태그를 추가합니다 (없으면 생성).",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_urn": {"type": "string"},
                "tag": {"type": "string"},
            },
            "required": ["entity_urn", "tag"],
        },
    },
    {
        "name": "add_owner",
        "description": "엔티티에 소유자를 추가합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_urn": {"type": "string"},
                "owner": {"type": "string", "description": "username 또는 corpuser/corpGroup URN"},
                "owner_type": {"type": "string", "default": "DATAOWNER",
                                "enum": ["DATAOWNER", "PRODUCER", "DEVELOPER", "CONSUMER",
                                         "STAKEHOLDER", "TECHNICAL_OWNER", "BUSINESS_OWNER"]},
            },
            "required": ["entity_urn", "owner"],
        },
    },
    {
        "name": "add_upstream_lineage",
        "description": "downstream 엔티티에 upstream 들을 연결합니다 (계보).",
        "parameters": {
            "type": "object",
            "properties": {
                "downstream_urn": {"type": "string"},
                "upstream_urns": {"type": "array", "items": {"type": "string"}},
                "lineage_type": {"type": "string", "default": "TRANSFORMED",
                                  "enum": ["TRANSFORMED", "COPY", "VIEW"]},
            },
            "required": ["downstream_urn", "upstream_urns"],
        },
    },
    {
        "name": "get_lineage",
        "description": "엔티티의 직접 상류/하류 URN 목록을 반환합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_urn": {"type": "string"},
                "direction": {"type": "string", "enum": ["UPSTREAM", "DOWNSTREAM", "BOTH"], "default": "BOTH"},
            },
            "required": ["entity_urn"],
        },
    },
    {
        "name": "list_entities",
        "description": "등록된 엔티티 URN 목록을 반환합니다 (entity_type 으로 필터링 가능).",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
    {
        "name": "stats",
        "description": "엔티티 타입별 개수 요약을 반환합니다.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "export_mcps",
        "description": "현재 카탈로그를 DataHub-호환 MCP JSONL 로 저장합니다 (실 DataHub ingest 가능).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "export_html",
        "description": "self-contained HTML 카탈로그 파일을 생성합니다 (보안 심사 통과 가능).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "title": {"type": "string", "default": "DataHub Lite Catalog"},
            },
            "required": ["path"],
        },
    },
]


def dispatch_tool(catalog: LocalCatalog, name: str, arguments: dict) -> Any:
    """LLM 이 호출한 tool 이름과 arguments 를 카탈로그 메서드로 분배.

    Returns: JSON 직렬화 가능한 결과. 에러는 {"error": "..."} 형태로 반환.
    """
    try:
        if name == "search_entities":
            return catalog.search(
                arguments.get("query", ""),
                entity_type=arguments.get("entity_type"),
                tags=arguments.get("tags") or (),
                limit=int(arguments.get("limit", 20)),
            )
        if name == "get_entity":
            return catalog.get_entity(arguments["entity_urn"])
        if name == "get_aspect":
            return catalog.get(arguments["entity_urn"], arguments["aspect_name"])
        if name == "upsert_aspect":
            ch = catalog.upsert(arguments["entity_urn"], arguments["aspect_name"], arguments["aspect"])
            return {"ok": True, "entityUrn": ch.entityUrn, "aspectName": ch.aspectName}
        if name == "register_dataset":
            urn = make_dataset_urn(arguments["platform"], arguments["name"], env=arguments.get("env", "PROD"))
            catalog.upsert_dataset_properties(
                urn,
                description=arguments.get("description"),
                name=arguments.get("name"),
            )
            if arguments.get("fields"):
                catalog.upsert_schema_metadata(urn, arguments["fields"], platform=arguments["platform"])
            for t in arguments.get("tags", []) or []:
                catalog.add_tag(urn, t)
            for o in arguments.get("owners", []) or []:
                catalog.add_owner(urn, o)
            return {"ok": True, "urn": urn}
        if name == "tag_entity":
            ch = catalog.add_tag(arguments["entity_urn"], arguments["tag"])
            return {"ok": True, "entityUrn": ch.entityUrn}
        if name == "add_owner":
            ch = catalog.add_owner(
                arguments["entity_urn"],
                arguments["owner"],
                owner_type=arguments.get("owner_type", "DATAOWNER"),
            )
            return {"ok": True, "entityUrn": ch.entityUrn}
        if name == "add_upstream_lineage":
            ch = catalog.add_upstream_lineage(
                arguments["downstream_urn"],
                arguments["upstream_urns"],
                lineage_type=arguments.get("lineage_type", "TRANSFORMED"),
            )
            return {"ok": True, "entityUrn": ch.entityUrn}
        if name == "get_lineage":
            urn = arguments["entity_urn"]
            direction = arguments.get("direction", "BOTH")
            out: dict[str, Any] = {"urn": urn}
            if direction in ("UPSTREAM", "BOTH"):
                out["upstream"] = catalog.get_upstream_urns(urn)
            if direction in ("DOWNSTREAM", "BOTH"):
                out["downstream"] = catalog.get_downstream_urns(urn)
            return out
        if name == "list_entities":
            return catalog.list_entities(arguments.get("entity_type"))[: int(arguments.get("limit", 100))]
        if name == "stats":
            return catalog.stats()
        if name == "export_mcps":
            p = catalog.export_mcps_jsonl(arguments["path"])
            return {"ok": True, "path": str(p)}
        if name == "export_html":
            p = catalog.export_html(arguments["path"], title=arguments.get("title", "DataHub Lite Catalog"))
            return {"ok": True, "path": str(p)}
        return {"error": f"알 수 없는 tool: {name}"}
    except Exception as e:  # noqa: BLE001 — LLM 에 에러를 넘겨 자가복구 유도
        logger.exception("dispatch_tool 실패: %s(%s)", name, arguments)
        return {"error": f"{type(e).__name__}: {e}"}


def tools_for_openai() -> list[dict]:
    """OpenAI Chat Completions 의 tools 스키마로 변환."""
    return [
        {"type": "function", "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        }} for t in TOOLS
    ]


def tools_for_anthropic() -> list[dict]:
    """Anthropic Messages API 의 tools 스키마로 변환 (`input_schema` 키 사용)."""
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in TOOLS
    ]


# ===== 8. self-contained HTML 카탈로그 렌더러 =====
# 모든 데이터는 <script id="catalog-data" type="application/json"> 안에 임베드.
# CSS/JS 인라인. 외부 fetch/CDN 없음 → 보안 심사 통과 가능.

_HTML_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>$TITLE</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
         margin: 0; background: #f7f8fa; color: #1f2937; }
  header { background: #0f172a; color: #f8fafc; padding: 16px 24px; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .meta { font-size: 12px; color: #94a3b8; margin-top: 4px; }
  .layout { display: grid; grid-template-columns: 320px 1fr; min-height: calc(100vh - 64px); }
  aside { background: #fff; border-right: 1px solid #e5e7eb; padding: 12px; overflow-y: auto; max-height: calc(100vh - 64px); }
  main { padding: 16px 24px; overflow-y: auto; max-height: calc(100vh - 64px); }
  input.search, select { width: 100%; padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; margin-bottom: 8px; }
  ul.entity-list { list-style: none; padding: 0; margin: 0; }
  ul.entity-list li { padding: 8px 10px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  ul.entity-list li:hover { background: #eef2ff; }
  ul.entity-list li.active { background: #4f46e5; color: white; }
  ul.entity-list li .etype { font-size: 11px; color: #6b7280; }
  ul.entity-list li.active .etype { color: #c7d2fe; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 11px; margin-right: 4px; }
  .stats { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .stats .card { background: white; padding: 10px 14px; border-radius: 6px; border: 1px solid #e5e7eb; min-width: 100px; }
  .stats .card .num { font-size: 20px; font-weight: 700; }
  .stats .card .lbl { font-size: 11px; color: #6b7280; text-transform: uppercase; }
  .urn { font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; color: #6b7280; word-break: break-all; }
  h2 { font-size: 16px; margin: 0 0 4px; }
  .aspect { background: white; border: 1px solid #e5e7eb; border-radius: 6px; margin-bottom: 12px; }
  .aspect-head { padding: 8px 12px; background: #f9fafb; border-bottom: 1px solid #e5e7eb; font-weight: 600; font-size: 13px; cursor: pointer; user-select: none; }
  .aspect-body { padding: 12px; font-family: ui-monospace, "SF Mono", monospace; font-size: 12px; white-space: pre-wrap; max-height: 600px; overflow: auto; }
  .lineage { background: white; border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px; margin-bottom: 12px; }
  .lineage h3 { margin: 0 0 8px; font-size: 13px; }
  .lineage .col { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
  .lineage a { color: #4f46e5; text-decoration: none; cursor: pointer; }
  .lineage a:hover { text-decoration: underline; }
  .empty { color: #9ca3af; font-style: italic; }
  table.fields { width: 100%; border-collapse: collapse; font-size: 12px; }
  table.fields th, table.fields td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #f1f5f9; }
  table.fields th { background: #f9fafb; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>$TITLE</h1>
  <div class="meta">DataHub Lite v$VERSION · DataHub 호환 v$DH_VER · 엔티티 $ENTITY_COUNT개 · 생성 $TIMESTAMP</div>
</header>
<div class="layout">
  <aside>
    <input class="search" id="q" placeholder="검색 (이름/URN/설명)…" autofocus>
    <select id="filter-type"><option value="">— 모든 타입 —</option></select>
    <ul class="entity-list" id="entity-list"></ul>
  </aside>
  <main id="detail"><p>왼쪽에서 엔티티를 선택하세요.</p></main>
</div>
<script type="application/json" id="catalog-data">$DATA</script>
<script>
(function() {
  var raw = document.getElementById('catalog-data').textContent;
  var data = JSON.parse(raw);
  var entities = data.entities;  // {urn: {entityType, aspects, displayName}}
  var urns = Object.keys(entities).sort();

  function escape(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function bestName(e) { return e.displayName || e.urn; }

  var typeSet = {};
  urns.forEach(function(u) { typeSet[entities[u].entityType] = (typeSet[entities[u].entityType]||0) + 1; });
  var typeSel = document.getElementById('filter-type');
  Object.keys(typeSet).sort().forEach(function(t) {
    var o = document.createElement('option');
    o.value = t; o.textContent = t + ' (' + typeSet[t] + ')';
    typeSel.appendChild(o);
  });

  function render() {
    var q = document.getElementById('q').value.toLowerCase().trim();
    var ft = typeSel.value;
    var filtered = urns.filter(function(u) {
      var e = entities[u];
      if (ft && e.entityType !== ft) return false;
      if (!q) return true;
      var hay = (u + ' ' + bestName(e) + ' ' + JSON.stringify(e.aspects)).toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    var list = document.getElementById('entity-list');
    list.innerHTML = '';
    filtered.slice(0, 500).forEach(function(u) {
      var e = entities[u];
      var li = document.createElement('li');
      li.dataset.urn = u;
      li.innerHTML = '<div>' + escape(bestName(e)) + '</div><div class="etype">' + escape(e.entityType) + '</div>';
      li.onclick = function() { showDetail(u); };
      list.appendChild(li);
    });
    if (filtered.length === 0) {
      list.innerHTML = '<li class="empty">결과 없음</li>';
    }
  }

  function showDetail(urn) {
    var e = entities[urn];
    Array.prototype.forEach.call(document.querySelectorAll('#entity-list li'), function(li) {
      li.classList.toggle('active', li.dataset.urn === urn);
    });
    var html = '<h2>' + escape(bestName(e)) + '</h2>';
    html += '<div class="urn">' + escape(urn) + '</div>';
    html += '<p><span class="badge">' + escape(e.entityType) + '</span></p>';

    // tags
    var tags = (e.aspects.globalTags && e.aspects.globalTags.tags) || [];
    if (tags.length) {
      html += '<p>';
      tags.forEach(function(t) { html += '<span class="badge">' + escape((t.tag||'').split(':').pop()) + '</span>'; });
      html += '</p>';
    }

    // lineage
    var ups = (e.aspects.upstreamLineage && e.aspects.upstreamLineage.upstreams) || [];
    var downs = data.downstream[urn] || [];
    if (ups.length || downs.length) {
      html += '<div class="lineage"><h3>계보 (Lineage)</h3>';
      html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">';
      html += '<div><b>↑ Upstream (' + ups.length + ')</b><div class="col">';
      ups.forEach(function(u) {
        html += '<a data-urn="' + escape(u.dataset||'') + '">' + escape(u.dataset||'') + '</a>';
      });
      if (!ups.length) html += '<span class="empty">없음</span>';
      html += '</div></div>';
      html += '<div><b>↓ Downstream (' + downs.length + ')</b><div class="col">';
      downs.forEach(function(d) {
        html += '<a data-urn="' + escape(d) + '">' + escape(d) + '</a>';
      });
      if (!downs.length) html += '<span class="empty">없음</span>';
      html += '</div></div></div></div>';
    }

    // schemaMetadata 우선 표시
    var sm = e.aspects.schemaMetadata;
    if (sm && sm.fields) {
      html += '<div class="aspect"><div class="aspect-head">스키마 (schemaMetadata · ' + sm.fields.length + ' fields)</div>';
      html += '<div class="aspect-body" style="font-family:inherit">';
      html += '<table class="fields"><thead><tr><th>필드</th><th>타입</th><th>설명</th></tr></thead><tbody>';
      sm.fields.forEach(function(f) {
        html += '<tr><td>' + escape(f.fieldPath) + '</td><td>' + escape(f.nativeDataType||'') + '</td><td>' + escape(f.description||'') + '</td></tr>';
      });
      html += '</tbody></table></div></div>';
    }

    // 나머지 aspects
    Object.keys(e.aspects).sort().forEach(function(a) {
      if (a === 'schemaMetadata') return;
      html += '<div class="aspect"><div class="aspect-head" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display===\\'none\\'?\\'block\\':\\'none\\'">' + escape(a) + '</div>';
      html += '<div class="aspect-body">' + escape(JSON.stringify(e.aspects[a], null, 2)) + '</div></div>';
    });

    var detail = document.getElementById('detail');
    detail.innerHTML = html;
    Array.prototype.forEach.call(detail.querySelectorAll('a[data-urn]'), function(a) {
      a.onclick = function() {
        var u = a.dataset.urn;
        if (entities[u]) showDetail(u);
      };
    });
  }

  document.getElementById('q').oninput = render;
  typeSel.onchange = render;
  render();
})();
</script>
</body>
</html>
"""


def _render_html_catalog(catalog: LocalCatalog, out_path: Path, *, title: str) -> Path:
    """카탈로그를 HTML 한 파일에 직렬화. 외부 의존 없이 즉시 브라우저로 열림."""
    # downstream 인덱스 미리 계산 (HTML 측 JS 가 빠르게 조회하도록)
    downstream: dict[str, list[str]] = {}
    for urn, aspects in catalog._aspects.items():
        ups = aspects.get("upstreamLineage", {}).get("upstreams", [])
        for u in ups:
            if isinstance(u, dict) and u.get("dataset"):
                downstream.setdefault(u["dataset"], []).append(urn)

    payload = {
        "entities": {
            urn: {
                "urn": urn,
                "entityType": catalog._types.get(urn, parse_urn(urn)[0]),
                "displayName": _best_display_name(aspects, urn),
                "aspects": aspects,
            }
            for urn, aspects in catalog._aspects.items()
        },
        "downstream": downstream,
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # </script> 토큰이 임베디드 데이터에 들어가면 HTML 이 깨지므로 안전 치환
    data_json = data_json.replace("</", "<\\/")

    html_text = (
        _HTML_TEMPLATE
        .replace("$TITLE", _html.escape(title))
        .replace("$VERSION", VERSION)
        .replace("$DH_VER", DATAHUB_COMPAT_VERSION)
        .replace("$ENTITY_COUNT", str(len(catalog._aspects)))
        .replace("$TIMESTAMP", _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        .replace("$DATA", data_json)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    return out_path


# ===== 9. Example Usage =====

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s :: %(message)s")
    cat = LocalCatalog()  # in-memory only

    # 1) Dataset 등록
    orders = make_dataset_urn("kafka", "orders", env="PROD")
    cat.upsert_dataset_properties(
        orders,
        description="원천 주문 이벤트 토픽",
        custom_properties={"retention.days": "30"},
    )
    cat.upsert_schema_metadata(orders, [
        {"fieldPath": "order_id", "type": "long", "description": "주문 고유 ID", "nullable": False},
        {"fieldPath": "user_id", "type": "long", "description": "사용자 ID"},
        {"fieldPath": "amount", "type": "double", "description": "결제 금액(KRW)"},
        {"fieldPath": "ts", "type": "timestamp", "description": "주문 발생 시각"},
    ])
    cat.add_tag(orders, "PII")
    cat.add_owner(orders, "alice", owner_type="TECHNICAL_OWNER")

    fact = make_dataset_urn("hive", "warehouse.fact_orders", env="PROD")
    cat.upsert_dataset_properties(fact, description="DW 주문 사실 테이블")
    cat.add_upstream_lineage(fact, [orders])
    cat.add_owner(fact, "bob")
    cat.set_domain(fact, "Sales")

    # 2) 검색
    print("\n[검색: 'orders']")
    for r in cat.search("orders"):
        print(f"  - {r['displayName']}  <{r['urn']}>")

    # 3) 계보
    print("\n[fact_orders 의 upstream]")
    for u in cat.get_upstream_urns(fact):
        print(f"  - {u}")

    # 4) Tool calling 시뮬레이션
    print("\n[tool: stats]")
    print(json.dumps(dispatch_tool(cat, "stats", {}), indent=2, ensure_ascii=False))

    # 5) export
    out_dir = Path(__file__).parent / "data"
    cat.export_mcps_jsonl(out_dir / "demo_mcps.jsonl")
    cat.export_html(out_dir / "demo_catalog.html")
    print(f"\n✓ MCP JSONL: {out_dir / 'demo_mcps.jsonl'}")
    print(f"✓ HTML 카탈로그: {out_dir / 'demo_catalog.html'}")

# Ontology v1 (tập MVP)

Rút từ `software-context.md` §3–4 theo dữ liệu thực tế (PLAN.md §4). Postgres là system of record; Neo4j là projection
(ADR-0002). Mọi node/edge có provenance về `EvidenceChunk` → `SourceSpan` → `DocumentVersion` (file hash).

## Node

| Nhóm | Label | Thuộc tính chính | Nguồn sinh |
|---|---|---|---|
| A | `LegalDocument:Circular` / `:QCVN` / `:ExternalStub` | `document_id`, `document_code`, `title`, `domain`, `text_status` | registry |
| A | `DocumentVersion` | `version_no`, `file_hash`, `format`, `page_count`, `registered_at` | registry |
| A | `RegistryFact` | `fact_type`, `value`, `source`, `declared_by`, `declared_at` | registry (ADR-0004) |
| B | `Chapter`, `Section`, `Article`, `Clause`, `Point`, `Appendix`, `Preamble`, `Paragraph` | `number`, `title`, `order`, `path`, `scope`, `domain` | parse_structure |
| B | `Table`, `TableRow`, `Footnote` | `table_code`, `row_no`, `row_path`, `is_group_header`, `normalized` (JSON) | parse_tables |
| B | `SourceSpan` | `file_hash`, `line_start/end`, `char_start/end`, `pdf_page_start/end`, `printed_page` | parser |
| C | `Definition` | `term`, `text` | parse_structure (Mục 1.3) |
| C | `TransitionRule` | `start_date`, `end_date`, `affected_scope`, `text` | extract_relations (TT06 Đ3–4, TT45 Đ3–4, TT10 Đ46) |
| C | `Condition` | `type`, `op`, `value`, `unit`, `text` | extract_rules |
| C | `Exception` | `text`, `scope` | extract_rules |
| C | `MonitoringRequirement` | `requirement_type` ∈ {sampling_condition, sampling_port, direct_measurement, method_precedence, cems, frequency_pointer}, `domain`, `text` | extract_relations (TT10) |
| C | `ApplicabilityRule` | `rule_code` (W-xx, E-xx, M-xx), `logic`, `version` | data/rules YAML |
| C | `Norm` (tùy chọn, `review_status`) | `norm_type`, `raw_text`, `confidence` | LLM đề xuất → review |
| D | `EnvironmentalDomain` | `code` | dictionaries |
| D | `IndustrySector` | `sector_id`, `vsic_codes`, `group_roman` | dictionaries (PL2) |
| D | `EquipmentType` | `equipment_id`, `path`, `fuel_type`, `capacity_condition_raw` | dictionaries (QCVN19 cột 4) |
| D | `Zone` | `domain`, `code`, `definition` | dictionaries |
| D | `ReceivingBodyType`, `FuelType` | `code` | dictionaries |
| E | `Parameter`, `ParameterGroup`, `Unit` | `parameter_id`, `phase`, `domains` / `group_id` / `unit_id` | dictionaries |
| E | `LimitRule` | `rule_code`, `zone`, `op`, `value`, `min`, `max`, `unit_id`, `ref_o2`, `review_status` | extract_rules (từ TableRow × zone) |
| E | `Method` | `code`, `stage`, `order` | parse_tables (PL1 hai QCVN, PL4 TT10) |
| E | `Coefficient`, `Formula` | `name`, `tef` / `latex`, `variables` | parse_tables (QCVN19 PL2/PL3, TT10 PL4.1) |
| F | `ApplicabilityAssessment`, `UserProvidedFact`, `MissingInformation` | ephemeral theo request | applicability engine |
| G | `EffectivePeriod` | `start_date`, `end_date`, `confidence` ∈ {from_text, declared} | registry + extract_relations |
| H | `EvidenceChunk` | `chunk_type` ∈ {clause, point, definition, table_row, footnote, transition_rule, preamble, registry_fact, monitoring_requirement}, `text`, `context_text`, `citation_label`, `domain`, `scope` | chunk |
| H | `ReviewTask`, `ReviewDecision`, `QueryLog`, `Answer`, `Citation`, `Feedback`, `AuditEvent`, `IndexVersion`, `EvalRun` | theo PLAN.md §4.1 | runtime |

Hoãn sang production: `Law/Decree/Decision` đầy đủ, `Facility/Project/Permit/Location` bền vững, `MonitoringProgram`, `Frequency` (ADR-0007), `SampleType`, `DischargePoint/EmissionPoint`, `InterpretationNote`.

## Edge

| Edge | Từ → Đến | Thuộc tính |
|---|---|---|
| `CONTAINS` | Document/Chapter/Section/Article/Clause/Appendix/Table → node con | `order` |
| `HAS_VERSION` | LegalDocument → DocumentVersion | |
| `HAS_REGISTRY_FACT` | LegalDocument → RegistryFact | |
| `HAS_SPAN` | node cấu trúc / TableRow / EvidenceChunk → SourceSpan | |
| `HAS_EVIDENCE` | LimitRule / TransitionRule / MonitoringRequirement / Definition / RegistryFact → EvidenceChunk | |
| `EXTRACTED_FROM` | EvidenceChunk → Clause/Point/TableRow/Footnote/… | |
| `PROMULGATES` / `PROMULGATED_BY` | Circular ↔ QCVN | `evidence` |
| `REPLACES` | QCVN → QCVN/ExternalStub | `scope` ∈ {full, partial}, `target_part` |
| `REPEALS` | Circular → Circular/QCVN/ExternalStub | `scope`, `target_part`, `condition` |
| `SUPPLEMENTS` | TT10 → QCVN | `evidence` |
| `REFERENCES` | node nội dung → LegalDocument/ExternalStub | `evidence` |
| `HAS_EFFECTIVE_PERIOD` | LegalDocument / LimitRule → EffectivePeriod | |
| `HAS_TRANSITION_RULE` | LegalDocument → TransitionRule | |
| `HAS_DEFINITION` | QCVN → Definition | |
| `GOVERNS_DOMAIN` | QCVN → EnvironmentalDomain | |
| `HAS_LIMIT_RULE` | QCVN / Table / TableRow → LimitRule | |
| `FOR_PARAMETER` | LimitRule / TableRow → Parameter | |
| `IN_UNIT` | LimitRule → Unit | |
| `IN_ZONE` | LimitRule → Zone | |
| `DEFINED_BY_TABLE_ROW` | LimitRule → TableRow | |
| `APPLIES_WHEN` | LimitRule / MonitoringRequirement → Condition | |
| `EXCEPT_WHEN` | LimitRule → Exception | |
| `APPLIES_TO` | LimitRule → EquipmentType / IndustrySector / ReceivingBodyType | |
| `QUALIFIED_BY` | TableRow / LimitRule → Footnote | |
| `PARENT_OF` | EquipmentType → EquipmentType; IndustrySector (group) → IndustrySector | |
| `USES_FUEL` | EquipmentType → FuelType | |
| `HAS_CHARACTERISTIC_PARAMETER` | IndustrySector → Parameter | `condition_text` (cột 5) |
| `HAS_METHOD` | Parameter → Method | `order`, `stage`, `document_id` |
| `BELONGS_TO_GROUP` | Parameter → ParameterGroup | |
| `HAS_UNIT` | Parameter → Unit | `default` |
| `USES_COEFFICIENT` / `USES_FORMULA` | Parameter (Dioxin/Furan, PAH) → Coefficient / Formula | |
| `HAS_MONITORING_REQUIREMENT` | QCVN / EnvironmentalDomain → MonitoringRequirement | |
| `SUPPORTED_BY` | ApplicabilityAssessment → EvidenceChunk / LimitRule / TransitionRule | |
| `CITES` | Answer → EvidenceChunk | `citation_label` |

## Ràng buộc Neo4j
- `CREATE CONSTRAINT … FOR (n:<Label>) REQUIRE n.id IS UNIQUE` cho mọi label.
- Index: `Parameter.parameter_id`, `EvidenceChunk.citation_label`, `LimitRule.rule_code`, `EquipmentType.equipment_id`.
- Projection build lại toàn bộ theo `index_version` bằng `MERGE`; không sửa tay trong Neo4j.

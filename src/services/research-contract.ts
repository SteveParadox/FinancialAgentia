/** Version 1 wire contract. CI checks Python exports and a real report round trip. */
import { z } from 'zod';

const FinancialSource = z.object({
  source_id: z.string(), provider: z.string(), source_url: z.string().nullable(),
  retrieved_at: z.string(), data_as_of: z.string().nullable(),
});
const FinancialEvidence = z.object({
  evidence_id: z.string(), source_id: z.string(), symbol: z.string(), metric: z.string(),
  value: z.string().refine(value => value.trim() !== '' && Number.isFinite(Number(value))),
  currency: z.string().nullable(), unit: z.string().nullable(),
  input_evidence_ids: z.array(z.string()), evidence_type: z.string(),
});
const FinancialClaim = z.object({
  claim_id: z.string(), statement: z.string(), metric: z.string(), value: z.string(),
  supporting_evidence_ids: z.array(z.string()).min(1), validation_status: z.literal('VERIFIED'),
});
export const ResearchReportSchema = z.object({
  schema_version: z.literal(1), execution_id: z.string().uuid(), session_id: z.string().uuid(),
  status: z.enum(['COMPLETE', 'PARTIAL', 'INSUFFICIENT_EVIDENCE']), answer: z.string().min(1),
  claims: z.array(FinancialClaim), evidence: z.array(FinancialEvidence),
  sources: z.array(FinancialSource), limitations: z.array(z.string()),
  agent_executions: z.array(z.object({
    agent_id: z.enum(['market', 'fundamental', 'news', 'quantitative', 'risk', 'research']),
    execution_id: z.string(), input_schema_version: z.literal(1), output_schema_version: z.literal(1),
    status: z.enum(['completed', 'failed', 'skipped']), tools: z.array(z.string()),
  })), researched_at: z.string(),
}).passthrough().superRefine((report, context) => {
  const sourceIds = new Set(report.sources.map(item => item.source_id));
  const evidenceIds = new Set(report.evidence.map(item => item.evidence_id));
  for (const row of report.evidence) {
    if (!sourceIds.has(row.source_id) || row.input_evidence_ids.some(id => !evidenceIds.has(id))) {
      context.addIssue({ code: 'custom', message: 'Unresolved evidence lineage' });
    }
  }
  for (const claim of report.claims) {
    if (claim.supporting_evidence_ids.some(id => !evidenceIds.has(id))) {
      context.addIssue({ code: 'custom', message: 'Unresolved claim evidence' });
    }
  }
});

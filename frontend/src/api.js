const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
const APP_API_TOKEN = import.meta.env.VITE_APP_API_TOKEN || '';

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (APP_API_TOKEN) headers.set('X-App-Token', APP_API_TOKEN);
  return fetch(url, { ...options, headers });
}

function stringifyDetail(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item;
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
        return field ? `${field}: ${item.msg}` : item.msg || JSON.stringify(item);
      })
      .join(' ');
  }
  if (detail && typeof detail === 'object') {
    return detail.message || detail.msg || JSON.stringify(detail);
  }
  return '';
}

async function parseError(response) {
  try {
    const body = await response.json();
    return stringifyDetail(body.detail || body.message) || `Request failed with ${response.status}`;
  } catch {
    return `Request failed with ${response.status}`;
  }
}

export async function getHealth() {
  const response = await apiFetch(`${API_BASE_URL}/health`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function getAccounts() {
  const response = await apiFetch(`${API_BASE_URL}/accounts`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function previewPeople(payload) {
  const response = await apiFetch(`${API_BASE_URL}/preview-people`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

function filenameFromDisposition(disposition) {
  if (!disposition) return 'apollo_leads.csv';
  const match = disposition.match(/filename="?([^"]+)"?/i);
  return match?.[1] || 'apollo_leads.csv';
}

async function downloadBlobResponse(response, fallbackFilename) {
  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition')) || fallbackFilename;
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || fallbackFilename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename || fallbackFilename;
}

export async function downloadCsv(payload) {
  const response = await apiFetch(`${API_BASE_URL}/download-csv`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));

  const messagesHeader = response.headers.get('X-Messages');
  const messages = messagesHeader ? JSON.parse(messagesHeader) : [];
  const verifiedCount = Number(response.headers.get('X-Verified-Email-Count') || 0);
  const accountUsed = Number(response.headers.get('X-Account-Used') || 0);
  await downloadBlobResponse(response, 'apollo_leads.csv');

  return { verifiedCount, accountUsed, messages };
}

export async function createCampaignFromPreview(payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/from-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function getCampaigns() {
  const response = await apiFetch(`${API_BASE_URL}/campaigns`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function downloadCampaignAudienceCsv(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/audience-csv`);
  if (!response.ok) throw new Error(await parseError(response));
  return downloadBlobResponse(response, `campaign_${campaignId}_audience.csv`);
}

export async function getCampaign(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function getSenders() {
  const response = await apiFetch(`${API_BASE_URL}/senders`);
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function updateRecipient(campaignId, recipientId, status) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/recipients/${recipientId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function generateDrafts(campaignId, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/generate-drafts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function updateMessage(campaignId, messageId, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/messages/${messageId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function updateTemplate(campaignId, stepNumber, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/templates/${stepNumber}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function saveSequenceTemplates(campaignId, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/templates`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function uploadAttachment(campaignId, file) {
  const data = new FormData();
  data.append('file', file);
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/attachments`, {
    method: 'POST',
    body: data,
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function deleteAttachment(campaignId, attachmentId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/attachments/${attachmentId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function updateSettings(campaignId, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/settings`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function launchCampaign(campaignId, payload) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/launch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function pauseCampaign(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/pause`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function resumeCampaign(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/resume`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function cancelCampaignRemaining(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/cancel-remaining`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function rescheduleCampaignOverdue(campaignId) {
  const response = await apiFetch(`${API_BASE_URL}/campaigns/${campaignId}/reschedule-overdue`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function schedulerTick() {
  const response = await apiFetch(`${API_BASE_URL}/scheduler/tick`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

export async function schedulerCheckReplies() {
  const response = await apiFetch(`${API_BASE_URL}/scheduler/check-replies`, { method: 'POST' });
  if (!response.ok) throw new Error(await parseError(response));
  return response.json();
}

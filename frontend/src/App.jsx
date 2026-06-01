import {
  Ban,
  Clock,
  Download,
  Eye,
  LayoutDashboard,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Reply,
  Save,
  Search,
  Send,
  WandSparkles,
  X,
} from 'lucide-react';
import React from 'react';
import { useEffect, useMemo, useState } from 'react';
import {
  createApolloAccount,
  createCampaignFromPreview,
  cancelCampaignRemaining,
  deleteAttachment,
  downloadCampaignAudienceCsv,
  downloadCsv,
  generateDrafts,
  getAccounts,
  getCampaign,
  getCampaigns,
  getHealth,
  getSenders,
  launchCampaign,
  pauseCampaign,
  previewPeople,
  rescheduleCampaignOverdue,
  resumeCampaign,
  saveSequenceTemplates,
  schedulerCheckReplies,
  schedulerTick,
  updateMessage,
  updateRecipient,
  updateSettings,
  updateTemplate,
  uploadAttachment,
} from './api.js';
import AccountSelector from './components/AccountSelector.jsx';
import PeopleTable from './components/PeopleTable.jsx';
import StatusBox from './components/StatusBox.jsx';
import TitleSelector from './components/TitleSelector.jsx';

const DEFAULT_SETTINGS = {
  sender_account_indexes: [],
  timezone: 'America/Los_Angeles',
  opening_days: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
  opening_start_time: '09:00',
  opening_end_time: '14:00',
  followup_days: ['Tuesday', 'Thursday'],
  followup_start_time: '09:00',
  followup_end_time: '14:00',
  min_followup_gap_days: 3,
  track_opens: true,
  stop_on_reply: true,
};

const DEFAULT_LOCATION_OPTIONS = [
  'United States',
  'Canada',
  'United Kingdom',
  'California',
  'New York',
  'San Francisco Bay Area',
  'Los Angeles',
  'Remote',
];

function formatDateTime(value) {
  if (!value) return 'None scheduled';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  });
}

function settingsFromCampaign(campaign, fallbackSenders = []) {
  if (!campaign) return DEFAULT_SETTINGS;
  return {
    sender_account_indexes: DEFAULT_SETTINGS.sender_account_indexes.length
      ? DEFAULT_SETTINGS.sender_account_indexes
      : fallbackSenders.map((sender) => sender.id),
    timezone: campaign.timezone || DEFAULT_SETTINGS.timezone,
    opening_days: campaign.opening_days || DEFAULT_SETTINGS.opening_days,
    opening_start_time: campaign.opening_start_time || DEFAULT_SETTINGS.opening_start_time,
    opening_end_time: campaign.opening_end_time || DEFAULT_SETTINGS.opening_end_time,
    followup_days: campaign.followup_days || DEFAULT_SETTINGS.followup_days,
    followup_start_time: campaign.followup_start_time || DEFAULT_SETTINGS.followup_start_time,
    followup_end_time: campaign.followup_end_time || DEFAULT_SETTINGS.followup_end_time,
    min_followup_gap_days: campaign.min_followup_gap_days || DEFAULT_SETTINGS.min_followup_gap_days,
    track_opens: Boolean(campaign.track_opens),
    stop_on_reply: Boolean(campaign.stop_on_reply),
  };
}

export default function App() {
  const [companyName, setCompanyName] = useState('');
  const [companyDomain, setCompanyDomain] = useState('');
  const [maxPeople, setMaxPeople] = useState(5000);
  const [locations, setLocations] = useState(['United States']);
  const [selectedTitles, setSelectedTitles] = useState([]);
  const [selectedAccount, setSelectedAccount] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [senders, setSenders] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [viewMode, setViewMode] = useState('extractor');
  const [health, setHealth] = useState(null);
  const [people, setPeople] = useState([]);
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState('');
  const [activity, setActivity] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [generatingDrafts, setGeneratingDrafts] = useState(false);
  const [verifiedCount, setVerifiedCount] = useState(null);
  const [campaignData, setCampaignData] = useState(null);
  const [sequenceStep, setSequenceStep] = useState(1);
  const [jobDescription, setJobDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [selectedRecipientId, setSelectedRecipientId] = useState(null);
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);

  const canPreview = useMemo(
    () => !previewing && maxPeople > 0 && (companyName.trim() || companyDomain.trim()),
    [companyName, companyDomain, previewing, maxPeople],
  );

  const campaign = campaignData?.campaign;
  const recipients = campaignData?.recipients || [];
  const sequenceMessages = campaignData?.messages || [];
  const templates = campaignData?.templates || [];
  const selectedRecipient = recipients.find((item) => item.id === selectedRecipientId) || recipients[0];
  const selectedMessages = selectedRecipient
    ? sequenceMessages.filter((item) => item.recipient_id === selectedRecipient.id)
    : [];

  const refreshBasics = async () => {
    const [healthResponse, accountResponse, senderResponse, campaignResponse] = await Promise.all([
      getHealth(),
      getAccounts(),
      getSenders(),
      getCampaigns(),
    ]);
    setHealth(healthResponse);
    setAccounts(accountResponse);
    setSenders(senderResponse);
    setCampaigns(campaignResponse);
    setSettings((current) => ({
      ...current,
      sender_account_indexes:
        current.sender_account_indexes.length > 0
          ? current.sender_account_indexes
          : senderResponse.map((sender) => sender.id),
    }));
  };

  useEffect(() => {
    refreshBasics().catch((err) => setError(err.message));
  }, []);

  const runTask = async (label, task) => {
    setActivity(label);
    setError('');
    try {
      await task();
    } catch (err) {
      setError(err.message);
    } finally {
      setActivity('');
    }
  };

  const loadCampaign = async (campaignId, targetStep = 5) => {
    await runTask('Loading sequence dashboard.', async () => {
      const [response, senderResponse] = await Promise.all([getCampaign(campaignId), getSenders()]);
      setCampaignData(response);
      setSenders(senderResponse);
      setSettings({
        ...settingsFromCampaign(response.campaign, senderResponse),
        sender_account_indexes: senderResponse
          .filter((sender) => sender.status !== 'disabled')
          .map((sender) => sender.id),
      });
      setSelectedRecipientId(response.recipients?.[0]?.id || null);
      setSequenceStep(targetStep);
      setViewMode('sequence');
      await refreshBasics();
    });
  };

  const handleCampaignAction = async (label, action, successMessage) => {
    if (!campaign) return;
    await runTask(label, async () => {
      const response = await action(campaign.id);
      setCampaignData(response);
      setMessages([successMessage]);
      await refreshBasics();
    });
  };

  const handlePreview = async () => {
    setPreviewing(true);
    await runTask('Searching Apollo. Preview does not reveal emails.', async () => {
      setMessages([]);
      setVerifiedCount(null);
      const response = await previewPeople({
        company_name: companyName,
        company_domain: companyDomain,
        titles: selectedTitles,
        locations,
        target_count: Number(maxPeople),
        apollo_account_index: selectedAccount,
      });
      setPeople(response.people);
      setMessages([`${response.count} people found.`, ...(response.messages || [])]);
      await refreshBasics();
    });
    setPreviewing(false);
  };

  const handleAddApolloAccount = async (payload) => {
    let createdAccount = null;
    await runTask('Adding Apollo account.', async () => {
      const response = await createApolloAccount(payload);
      createdAccount = response.account;
      setAccounts(response.accounts || []);
      setSelectedAccount(response.account.account_index);
      setMessages([`${response.account.account_email || 'Apollo account'} added and selected.`]);
      await refreshBasics();
    });
    return createdAccount;
  };

  const handleDownload = async () => {
    setDownloading(true);
    await runTask('Revealing verified emails and preparing CSV.', async () => {
      const response = await downloadCsv({ people, apollo_account_index: selectedAccount });
      setVerifiedCount(response.verifiedCount);
      setMessages(response.messages || []);
      await refreshBasics();
    });
    setDownloading(false);
  };

  const handleCreateSequence = async () => {
    setFinalizing(true);
    await runTask('Revealing verified emails and creating campaign audience.', async () => {
      const response = await createCampaignFromPreview({
        people,
        apollo_account_index: selectedAccount,
        name: `${companyName || companyDomain || 'Apollo'} Outreach Sequence`,
      });
      setCampaignData(response);
      setSelectedRecipientId(response.recipients?.[0]?.id || null);
      setSequenceStep(1);
      setViewMode('sequence');
      const nextMessages = [`${response.recipients.length} verified recipients added to campaign.`];
      try {
        const filename = await downloadCampaignAudienceCsv(response.campaign.id);
        nextMessages.push(`Audience CSV downloaded and archived locally as ${filename}.`);
      } catch (downloadError) {
        nextMessages.push(`Campaign created, but the audience CSV auto-download failed: ${downloadError.message}`);
      }
      setMessages(nextMessages);
      await refreshBasics();
    });
    setFinalizing(false);
  };

  const handleDownloadAudienceCsv = async () => {
    await runTask('Downloading saved campaign audience CSV.', async () => {
      const filename = await downloadCampaignAudienceCsv(campaign.id);
      setMessages([`Audience CSV downloaded from the saved campaign archive: ${filename}.`]);
    });
  };

  const handleRemoveRecipient = async (recipientId) => {
    await runTask('Updating audience.', async () => {
      const response = await updateRecipient(campaign.id, recipientId, 'removed');
      setCampaignData(response);
    });
  };

  const handleGenerateDrafts = async () => {
    setGeneratingDrafts(true);
    await runTask('Generating draft suggestion with Claude.', async () => {
      const response = await generateDrafts(campaign.id, {
        job_description: jobDescription,
        instructions,
      });
      setCampaignData(response);
      setSelectedRecipientId(response.recipients?.[0]?.id || null);
      setSequenceStep(2);
      setMessages(['Draft suggestion generated. Edit it, then save the templates for everyone.']);
    });
    setGeneratingDrafts(false);
  };

  const handleSaveSequenceTemplates = async (payload) => {
    await runTask('Saving templates for every recipient.', async () => {
      const response = await saveSequenceTemplates(campaign.id, payload);
      setCampaignData(response);
      setSelectedRecipientId(response.recipients?.[0]?.id || null);
      setMessages(['Templates saved. Preview now shows each person with names filled in.']);
      setSequenceStep(3);
    });
  };

  const handleUploadAttachment = async (file) => {
    await runTask('Uploading attachment.', async () => {
      const response = await uploadAttachment(campaign.id, file);
      setCampaignData(response);
    });
  };

  const handleDeleteAttachment = async (attachmentId) => {
    await runTask('Removing attachment.', async () => {
      const response = await deleteAttachment(campaign.id, attachmentId);
      setCampaignData(response);
    });
  };

  const handleSaveMessage = async (message) => {
    await runTask('Saving draft.', async () => {
      const response = await updateMessage(campaign.id, message.id, {
        subject: message.subject,
        body_text: message.body_text,
      });
      setCampaignData(response);
    });
  };

  const handleSaveTemplate = async (template) => {
    await runTask('Saving template and updating all recipient drafts.', async () => {
      const response = await updateTemplate(campaign.id, template.step_number, {
        subject_template: template.subject_template,
        body_template: template.body_template,
      });
      setCampaignData(response);
      setMessages([`Template ${template.step_number} saved. All unsent recipient emails were updated.`]);
    });
  };

  const handleLaunch = async () => {
    await runTask('Launching sequence with default safety schedule.', async () => {
      const response = await launchCampaign(campaign.id, settings);
      setCampaignData(response);
      await schedulerTick();
      await refreshBasics();
      setMessages(['Sequence launched. Scheduler will send only inside configured windows.']);
    });
  };

  const handleSaveSettings = async () => {
    await runTask('Saving schedule settings.', async () => {
      const response = await updateSettings(campaign.id, settings);
      setCampaignData(response);
      setMessages(['Settings saved.']);
    });
  };

  const handlePauseCampaign = () =>
    handleCampaignAction(
      'Pausing sequence.',
      pauseCampaign,
      'Sequence paused. Scheduled emails will not send until resumed.',
    );

  const handleResumeCampaign = () =>
    handleCampaignAction(
      'Resuming sequence.',
      resumeCampaign,
      'Sequence resumed from the saved queue.',
    );

  const handleCancelRemaining = () =>
    handleCampaignAction(
      'Canceling remaining unsent emails.',
      cancelCampaignRemaining,
      'Remaining draft and scheduled emails were canceled.',
    );

  const handleRescheduleOverdue = () =>
    handleCampaignAction(
      'Moving overdue emails to the next valid window.',
      rescheduleCampaignOverdue,
      'Overdue scheduled emails were moved to the next valid sending window.',
    );

  const handleCheckReplies = () =>
    handleCampaignAction(
      'Checking Gmail for replies and bounces.',
      async (campaignId) => {
        await schedulerCheckReplies();
        return getCampaign(campaignId);
      },
      'Reply and bounce check finished.',
    );

  const handleRunScheduler = () =>
    handleCampaignAction(
      'Running one scheduler check.',
      async (campaignId) => {
        const result = await schedulerTick();
        const response = await getCampaign(campaignId);
        response.scheduler_result = result;
        return response;
      },
      'Scheduler check finished.',
    );

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Local Apollo workflow</p>
          <h1>{viewMode === 'dashboard' ? 'Sequence Dashboard' : campaign ? 'Lead Sequence Builder' : 'Lead Extractor'}</h1>
        </div>
        <div className="header-actions">
          <button className="small-button secondary-button" type="button" onClick={() => {
            setCampaignData(null);
            setViewMode('extractor');
          }}>
            <Plus size={16} />
            New Search
          </button>
          <button className="small-button secondary-button" type="button" onClick={() => {
            setCampaignData(null);
            setViewMode('dashboard');
            refreshBasics().catch((err) => setError(err.message));
          }}>
            <LayoutDashboard size={16} />
            Sequences
          </button>
          <div className="count-pill">
            {campaign ? `${recipients.length} recipients` : viewMode === 'dashboard' ? `${campaigns.length} sequences` : people.length > 0 ? `${people.length} people found` : 'No preview yet'}
          </div>
        </div>
      </header>

      <StatusBox error={error} messages={messages} health={health} accounts={accounts} activity={activity} />

      {viewMode === 'dashboard' && !campaign && (
        <CampaignDashboard
          campaigns={campaigns}
          onOpen={(campaignId) => loadCampaign(campaignId, 5)}
          onRefresh={() => refreshBasics().catch((err) => setError(err.message))}
        />
      )}

      {viewMode === 'extractor' && !campaign && (
        <Extractor
          companyName={companyName}
          setCompanyName={setCompanyName}
          companyDomain={companyDomain}
          setCompanyDomain={setCompanyDomain}
          maxPeople={maxPeople}
          setMaxPeople={setMaxPeople}
          locations={locations}
          setLocations={setLocations}
          selectedTitles={selectedTitles}
          setSelectedTitles={setSelectedTitles}
          accounts={accounts}
          selectedAccount={selectedAccount}
          setSelectedAccount={setSelectedAccount}
          onAddApolloAccount={handleAddApolloAccount}
          canPreview={canPreview}
          handlePreview={handlePreview}
          handleDownload={handleDownload}
          handleCreateSequence={handleCreateSequence}
          previewing={previewing}
          downloading={downloading}
          finalizing={finalizing}
          verifiedCount={verifiedCount}
          people={people}
        />
      )}

      {campaign && (
        <SequenceWizard
          sequenceStep={sequenceStep}
          setSequenceStep={setSequenceStep}
          campaignData={campaignData}
          recipients={recipients}
          selectedRecipient={selectedRecipient}
          setSelectedRecipientId={setSelectedRecipientId}
          selectedMessages={selectedMessages}
          setCampaignData={setCampaignData}
          jobDescription={jobDescription}
          setJobDescription={setJobDescription}
          instructions={instructions}
          setInstructions={setInstructions}
          templates={templates}
          onSaveTemplates={handleSaveSequenceTemplates}
          handleRemoveRecipient={handleRemoveRecipient}
          handleDownloadAudienceCsv={handleDownloadAudienceCsv}
          handleGenerateDrafts={handleGenerateDrafts}
          generatingDrafts={generatingDrafts}
          attachments={campaignData.attachments || []}
          onUploadAttachment={handleUploadAttachment}
          onDeleteAttachment={handleDeleteAttachment}
          handleSaveMessage={handleSaveMessage}
          handleSaveTemplate={handleSaveTemplate}
          settings={settings}
          setSettings={setSettings}
          senders={senders}
          handleSaveSettings={handleSaveSettings}
          handleLaunch={handleLaunch}
          handlePauseCampaign={handlePauseCampaign}
          handleResumeCampaign={handleResumeCampaign}
          handleCancelRemaining={handleCancelRemaining}
          handleRescheduleOverdue={handleRescheduleOverdue}
          handleCheckReplies={handleCheckReplies}
          handleRunScheduler={handleRunScheduler}
        />
      )}
    </main>
  );
}

function CampaignDashboard({ campaigns, onOpen, onRefresh }) {
  return (
    <section className="sequence-shell">
      <div className="panel-heading">
        <div>
          <h2>Sequences</h2>
          <p className="panel-subtitle">Monitor every campaign before trusting the sender loop.</p>
        </div>
        <button className="small-button" type="button" onClick={onRefresh}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </div>
      <div className="dashboard-grid">
        {campaigns.length === 0 && <div className="empty-state">No sequences yet.</div>}
        {campaigns.map((item) => {
          const stats = item.stats || {};
          const totalMessages =
            (stats.draft || 0) +
            (stats.scheduled || 0) +
            (stats.sent || 0) +
            (stats.opened || 0) +
            (stats.replied || 0) +
            (stats.skipped || 0) +
            (stats.failed || 0) +
            (stats.canceled || 0);
          const completed = (stats.sent || 0) + (stats.opened || 0) + (stats.replied || 0) + (stats.skipped || 0) + (stats.canceled || 0);
          const progress = totalMessages ? Math.round((completed / totalMessages) * 100) : 0;
          return (
            <article className="campaign-card" key={item.id}>
              <div className="campaign-card-top">
                <div>
                  <h3>{item.name}</h3>
                  <span className={`status-pill status-${item.status}`}>{item.status}</span>
                </div>
                <button className="small-button" type="button" onClick={() => onOpen(item.id)}>
                  Open
                </button>
              </div>
              <div className="progress-meter">
                <span style={{ width: `${progress}%` }} />
              </div>
              <div className="metric-grid">
                <Metric label="Recipients" value={stats.active_recipients || item.recipient_count || 0} />
                <Metric label="Scheduled" value={stats.scheduled || 0} />
                <Metric label="Sent" value={(stats.sent || 0) + (stats.opened || 0) + (stats.replied || 0)} />
                <Metric label="Opened" value={(stats.opened || 0) + (stats.replied || 0)} />
                <Metric label="Replied" value={stats.replied_recipients || 0} />
                <Metric label="Failed" value={stats.failed || 0} />
              </div>
              <div className="next-send-line">
                <Clock size={16} />
                Next send: {formatDateTime(stats.next_scheduled_at)}
              </div>
              {stats.due_now > 0 && (
                <div className="warning-line">{stats.due_now} scheduled emails are due now.</div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric-box">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Extractor(props) {
  return (
    <section className="workspace">
      <form className="controls" onSubmit={(event) => event.preventDefault()}>
        <div className="field-grid">
          <section className="field-group">
            <label htmlFor="company-name">Company name</label>
            <input id="company-name" value={props.companyName} onChange={(event) => props.setCompanyName(event.target.value)} placeholder="OpenAI" />
          </section>
          <section className="field-group">
            <label htmlFor="company-domain">Company domain</label>
            <input id="company-domain" value={props.companyDomain} onChange={(event) => props.setCompanyDomain(event.target.value)} placeholder="openai.com" />
          </section>
          <section className="field-group">
            <label htmlFor="max-people">Max people</label>
            <input id="max-people" type="number" min="1" max="5000" value={props.maxPeople} onChange={(event) => props.setMaxPeople(event.target.value)} />
          </section>
          <AccountSelector
            accounts={props.accounts}
            value={props.selectedAccount}
            onChange={props.setSelectedAccount}
            onAddAccount={props.onAddApolloAccount}
          />
        </div>

        <LocationSelector locations={props.locations} onChange={props.setLocations} />

        <TitleSelector selectedTitles={props.selectedTitles} onChange={props.setSelectedTitles} />

        <div className="actions">
          <button type="button" onClick={props.handlePreview} disabled={!props.canPreview}>
            <Search size={18} />
            {props.previewing ? 'Previewing...' : 'Preview People'}
          </button>
          <button type="button" onClick={props.handleDownload} disabled={props.people.length === 0 || props.downloading}>
            <Download size={18} />
            {props.downloading ? 'Preparing CSV...' : 'Download CSV'}
          </button>
        </div>
        <button className="wide-button" type="button" onClick={props.handleCreateSequence} disabled={props.people.length === 0 || props.finalizing}>
          <WandSparkles size={18} />
          {props.finalizing ? 'Creating Sequence...' : 'Create Email Sequence'}
        </button>
        {props.verifiedCount !== null && <p className="export-result">{props.verifiedCount} verified emails exported</p>}
      </form>

      <section className="preview-panel">
        <div className="panel-heading">
          <h2>Preview</h2>
          <span>{props.people.length} people found</span>
        </div>
        <PeopleTable people={props.people} loading={props.previewing} />
      </section>
    </section>
  );
}

function LocationSelector({ locations, onChange }) {
  const [customLocation, setCustomLocation] = useState('');

  const addLocation = (location) => {
    const trimmed = location.trim();
    if (!trimmed) return;
    const exists = locations.some((item) => item.toLowerCase() === trimmed.toLowerCase());
    if (!exists) onChange([...locations, trimmed]);
    setCustomLocation('');
  };

  const removeLocation = (location) => {
    onChange(locations.filter((item) => item !== location));
  };

  return (
    <section className="field-group location-control">
      <div className="filter-heading">
        <label>Location</label>
        <span>{locations.length} selected</span>
      </div>
      <div className="selected-filter-row">
        <span>Person Locations:</span>
        {locations.length === 0 && <em>Global search</em>}
        {locations.map((location) => (
          <button className="filter-chip" type="button" key={location} onClick={() => removeLocation(location)}>
            {location}
            <X size={14} />
          </button>
        ))}
      </div>
      <div className="chip-row">
        {DEFAULT_LOCATION_OPTIONS.map((location) => (
          <button
            className="chip"
            type="button"
            key={location}
            disabled={locations.some((item) => item.toLowerCase() === location.toLowerCase())}
            onClick={() => addLocation(location)}
          >
            {location}
          </button>
        ))}
      </div>
      <div className="inline-row">
        <input
          value={customLocation}
          onChange={(event) => setCustomLocation(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              addLocation(customLocation);
            }
          }}
          placeholder="Add city, state, or country"
        />
        <button className="icon-button" type="button" onClick={() => addLocation(customLocation)}>
          <Plus size={20} />
        </button>
      </div>
    </section>
  );
}

function SequenceWizard(props) {
  const tabs = ['Audience', 'Content', 'Preview', 'Settings', 'Monitor'];
  return (
    <section className="sequence-shell">
      <nav className="sequence-tabs">
        {tabs.map((tab, index) => (
          <button
            key={tab}
            type="button"
            className={props.sequenceStep === index + 1 ? 'tab-active' : ''}
            onClick={() => props.setSequenceStep(index + 1)}
          >
            <span>{index + 1}</span>
            {tab}
          </button>
        ))}
      </nav>

      {props.sequenceStep === 1 && (
        <AudienceStep
          recipients={props.recipients}
          onRemove={props.handleRemoveRecipient}
          onDownloadAudienceCsv={props.handleDownloadAudienceCsv}
        />
      )}
      {props.sequenceStep === 2 && (
        <ContentStep
          jobDescription={props.jobDescription}
          setJobDescription={props.setJobDescription}
          instructions={props.instructions}
          setInstructions={props.setInstructions}
          templates={props.templates}
          onSaveTemplates={props.onSaveTemplates}
          onGenerate={props.handleGenerateDrafts}
          generatingDrafts={props.generatingDrafts}
          attachments={props.attachments || []}
          onUploadAttachment={props.onUploadAttachment}
          onDeleteAttachment={props.onDeleteAttachment}
        />
      )}
      {props.sequenceStep === 3 && (
        <PreviewStep
          recipients={props.recipients}
          templates={props.templates}
          selectedRecipient={props.selectedRecipient}
          setSelectedRecipientId={props.setSelectedRecipientId}
          messages={props.selectedMessages}
          setCampaignData={props.setCampaignData}
          campaignData={props.campaignData}
          onSave={props.handleSaveMessage}
          onSaveTemplate={props.handleSaveTemplate}
        />
      )}
      {props.sequenceStep === 4 && (
        <SettingsStep
          settings={props.settings}
          setSettings={props.setSettings}
          senders={props.senders}
          stats={props.campaignData.stats}
          onSave={props.handleSaveSettings}
          onLaunch={props.handleLaunch}
        />
      )}
      {props.sequenceStep === 5 && (
        <MonitorStep
          campaignData={props.campaignData}
          recipients={props.recipients}
          senders={props.senders}
          onPause={props.handlePauseCampaign}
          onResume={props.handleResumeCampaign}
          onCancelRemaining={props.handleCancelRemaining}
          onRescheduleOverdue={props.handleRescheduleOverdue}
          onCheckReplies={props.handleCheckReplies}
          onRunScheduler={props.handleRunScheduler}
        />
      )}
    </section>
  );
}

function AudienceStep({ recipients, onRemove, onDownloadAudienceCsv }) {
  const active = recipients.filter((recipient) => recipient.status !== 'removed');
  return (
    <section className="sequence-panel">
      <div className="panel-heading">
        <h2>Audience</h2>
        <span>{active.length} active recipients</span>
      </div>
      <div className="panel-actions">
        <button className="small-button" type="button" onClick={onDownloadAudienceCsv}>
          <Download size={16} />
          Download audience CSV
        </button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Title</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {recipients.map((recipient) => (
              <tr key={recipient.id} className={recipient.status === 'removed' ? 'row-muted' : ''}>
                <td>{recipient.first_name} {recipient.last_name}</td>
                <td>{recipient.email}</td>
                <td>{recipient.title}</td>
                <td>{recipient.status}</td>
                <td>
                  {recipient.status !== 'removed' && (
                    <button className="small-button" type="button" onClick={() => onRemove(recipient.id)}>Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ContentStep({
  jobDescription,
  setJobDescription,
  instructions,
  setInstructions,
  templates,
  onSaveTemplates,
  onGenerate,
  generatingDrafts,
  attachments,
  onUploadAttachment,
  onDeleteAttachment,
}) {
  const templateFor = (stepNumber) =>
    (templates || []).find((template) => template.step_number === stepNumber) || {};
  const [subjectTemplate, setSubjectTemplate] = useState('');
  const [mainBodyTemplate, setMainBodyTemplate] = useState('');
  const [followup1BodyTemplate, setFollowup1BodyTemplate] = useState('');
  const [followup2BodyTemplate, setFollowup2BodyTemplate] = useState('');

  useEffect(() => {
    const main = templateFor(1);
    const followup1 = templateFor(2);
    const followup2 = templateFor(3);
    setSubjectTemplate(main.subject_template || '');
    setMainBodyTemplate(main.body_template || '');
    setFollowup1BodyTemplate(followup1.body_template || '');
    setFollowup2BodyTemplate(followup2.body_template || '');
  }, [templates]);

  const savePayload = {
    subject_template: subjectTemplate,
    main_body_template: mainBodyTemplate,
    followup_1_body_template: followup1BodyTemplate,
    followup_2_body_template: followup2BodyTemplate,
  };

  return (
    <section className="sequence-panel form-panel">
      <h2>Content</h2>
      <section className="field-group">
        <label>Main subject</label>
        <input
          value={subjectTemplate}
          onChange={(event) => setSubjectTemplate(event.target.value)}
          placeholder="quick question"
        />
      </section>
      <section className="field-group">
        <label>Main email</label>
        <textarea
          value={mainBodyTemplate}
          onChange={(event) => setMainBodyTemplate(event.target.value)}
          placeholder={'Hi {{first_name}},\n\nPaste your main email here.\n\nBest,\nSahaj'}
        />
      </section>
      <section className="field-group">
        <label>Follow-up 1</label>
        <textarea
          value={followup1BodyTemplate}
          onChange={(event) => setFollowup1BodyTemplate(event.target.value)}
          placeholder={'Hi {{first_name}},\n\nPaste your first follow-up here.\n\nBest,\nSahaj'}
        />
      </section>
      <section className="field-group">
        <label>Follow-up 2</label>
        <textarea
          value={followup2BodyTemplate}
          onChange={(event) => setFollowup2BodyTemplate(event.target.value)}
          placeholder={'Hi {{first_name}},\n\nPaste your final follow-up here.\n\nBest,\nSahaj'}
        />
      </section>
      <div className="chip-row">
        <button type="button" onClick={() => onSaveTemplates(savePayload)}>
          <Save size={18} />
          Save templates for everyone
        </button>
      </div>
      <section className="field-group">
        <label>Draft suggestion context</label>
        <textarea value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} placeholder="Optional: paste the job description if you want Claude to suggest a draft" />
      </section>
      <section className="field-group">
        <label>Draft suggestion instructions</label>
        <textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Optional: paste your resume/master data or extra instructions for Claude" />
      </section>
      <button type="button" onClick={onGenerate} disabled={generatingDrafts}>
        <WandSparkles size={18} />
        {generatingDrafts ? 'Generating...' : 'Generate draft suggestion'}
      </button>
      <section className="field-group">
        <label>Attachments sent to recipients</label>
        <input
          type="file"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUploadAttachment(file);
            event.target.value = '';
          }}
        />
        <div className="attachment-list">
          {attachments.length === 0 && <span className="muted">No attachments added.</span>}
          {attachments.map((attachment) => (
            <div className="attachment-row" key={attachment.id}>
              <span>{attachment.filename}</span>
              <span>{Math.ceil((attachment.size_bytes || 0) / 1024)} KB</span>
              <button className="small-button" type="button" onClick={() => onDeleteAttachment(attachment.id)}>
                Remove
              </button>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

function PreviewStep({
  recipients,
  templates,
  selectedRecipient,
  setSelectedRecipientId,
  messages,
}) {
  const renderForRecipient = (text) => {
    const recipient = selectedRecipient || {};
    return (text || '')
      .replaceAll('{{first_name}}', recipient.first_name || 'there')
      .replaceAll('{{last_name}}', recipient.last_name || '');
  };

  return (
    <section className="sequence-preview">
      <aside className="recipient-list">
        {recipients.filter((item) => item.status !== 'removed').map((recipient) => (
          <button
            key={recipient.id}
            type="button"
            className={recipient.id === selectedRecipient?.id ? 'recipient-active' : ''}
            onClick={() => setSelectedRecipientId(recipient.id)}
          >
            {recipient.first_name} {recipient.last_name}
            <span>{recipient.email}</span>
          </button>
        ))}
      </aside>
      <section className="message-stack">
        {templates.length === 0 && <div className="empty-state">Save templates in Content first.</div>}
        {templates.map((template) => {
          const previewMessage = messages.find((message) => message.step_number === template.step_number);
          return (
          <article className="message-card" key={template.id || template.step_number}>
            <div className="panel-heading">
              <h2>{template.step_name}</h2>
              <span>{previewMessage?.status || 'template'}</span>
            </div>
            <div className="template-preview">
              <strong>Preview for {selectedRecipient?.first_name || 'selected recipient'}</strong>
              <p>{renderForRecipient(template.subject_template)}</p>
              <pre>{renderForRecipient(template.body_template)}</pre>
            </div>
          </article>
          );
        })}
      </section>
    </section>
  );
}

function SettingsStep({ settings, setSettings, senders, stats, onSave, onLaunch }) {
  const toggleSender = (senderId) => {
    const exists = settings.sender_account_indexes.includes(senderId);
    setSettings({
      ...settings,
      sender_account_indexes: exists
        ? settings.sender_account_indexes.filter((id) => id !== senderId)
        : [...settings.sender_account_indexes, senderId],
    });
  };
  return (
    <section className="sequence-panel form-panel">
      <h2>Settings</h2>
      <div className="settings-grid">
        <section>
          <h3>Sender accounts</h3>
          {senders.map((sender) => (
            <label className="check-row" key={sender.id}>
              <input type="checkbox" checked={settings.sender_account_indexes.includes(sender.id)} onChange={() => toggleSender(sender.id)} />
              <span>{sender.email} · {sender.status} · {sender.sent_today}/{sender.daily_limit} today</span>
            </label>
          ))}
        </section>
        <section>
          <h3>Default schedule</h3>
          <p>Opening emails: Monday-Friday, 9:00 AM-2:00 PM PDT.</p>
          <p>Follow-ups: Tuesdays and Thursdays, 9:00 AM-2:00 PM PDT, only after &gt;3 days.</p>
          <p>Daily limit: 400 total emails per sender account unless edited in `.env` or sender settings.</p>
        </section>
      </div>
      <section className="settings-grid">
        <label className="check-row">
          <input type="checkbox" checked={settings.track_opens} onChange={(event) => setSettings({ ...settings, track_opens: event.target.checked })} />
          <span>Track opens with hidden pixel</span>
        </label>
        <label className="check-row">
          <input type="checkbox" checked={settings.stop_on_reply} onChange={(event) => setSettings({ ...settings, stop_on_reply: event.target.checked })} />
          <span>Stop follow-ups after reply</span>
        </label>
      </section>
      <div className="stats-row">
        <span>Drafts: {stats?.draft || 0}</span>
        <span>Scheduled: {stats?.scheduled || 0}</span>
        <span>Sent: {stats?.sent || 0}</span>
        <span>Opened: {stats?.opened || 0}</span>
        <span>Skipped: {stats?.skipped || 0}</span>
      </div>
      <div className="actions">
        <button type="button" onClick={onSave}>
          <Save size={18} />
          Save Settings
        </button>
        <button type="button" onClick={onLaunch}>
          <Send size={18} />
          Launch Sequence
        </button>
      </div>
    </section>
  );
}

function MonitorStep({
  campaignData,
  recipients,
  senders,
  onPause,
  onResume,
  onCancelRemaining,
  onRescheduleOverdue,
  onCheckReplies,
  onRunScheduler,
}) {
  const campaign = campaignData.campaign;
  const stats = campaignData.stats || {};
  const messages = campaignData.messages || [];
  const events = campaignData.events || [];
  const senderById = Object.fromEntries(senders.map((sender) => [sender.id, sender]));
  const messagesByRecipient = messages.reduce((acc, message) => {
    acc[message.recipient_id] = acc[message.recipient_id] || [];
    acc[message.recipient_id].push(message);
    return acc;
  }, {});

  return (
    <section className="sequence-panel monitor-panel">
      <div className="monitor-hero">
        <div>
          <p className="eyebrow">Sequence monitor</p>
          <h2>{campaign.name}</h2>
          <div className="monitor-meta">
            <span className={`status-pill status-${campaign.status}`}>{campaign.status}</span>
            <span>Next send: {formatDateTime(stats.next_scheduled_at)}</span>
            <span>Last sent: {formatDateTime(stats.last_sent_at)}</span>
          </div>
        </div>
        <div className="monitor-actions">
          {campaign.status === 'paused' ? (
            <button type="button" onClick={onResume}>
              <Play size={18} />
              Resume
            </button>
          ) : (
            <button type="button" onClick={onPause}>
              <Pause size={18} />
              Pause
            </button>
          )}
          <button className="secondary-button" type="button" onClick={onRescheduleOverdue}>
            <Clock size={18} />
            Move Overdue
          </button>
          <button className="secondary-button" type="button" onClick={onCheckReplies}>
            <Reply size={18} />
            Check Replies
          </button>
          <button className="secondary-button" type="button" onClick={onRunScheduler}>
            <RefreshCw size={18} />
            Run Check
          </button>
          <button className="danger-button" type="button" onClick={onCancelRemaining}>
            <Ban size={18} />
            Cancel Remaining
          </button>
        </div>
      </div>

      {stats.due_now > 0 && (
        <div className="warning-box">
          {stats.due_now} emails are due right now. Use Move Overdue before running the scheduler if you missed the window.
        </div>
      )}

      <div className="metric-grid monitor-metrics">
        <Metric label="Recipients" value={stats.active_recipients || 0} />
        <Metric label="Drafts" value={stats.draft || 0} />
        <Metric label="Scheduled" value={stats.scheduled || 0} />
        <Metric label="Sent" value={(stats.sent || 0) + (stats.opened || 0) + (stats.replied || 0)} />
        <Metric label="Opened" value={(stats.opened || 0) + (stats.replied || 0)} />
        <Metric label="Open events" value={stats.open_events || 0} />
        <Metric label="Replied" value={stats.replied_recipients || 0} />
        <Metric label="Bounced" value={stats.bounced_recipients || 0} />
        <Metric label="Skipped" value={stats.skipped || 0} />
        <Metric label="Failed" value={stats.failed || 0} />
      </div>

      <section className="sender-strip">
        {senders.map((sender) => (
          <div className="sender-card" key={sender.id}>
            <strong>{sender.email}</strong>
            <span>{sender.status}</span>
            <span>{sender.sent_today}/{sender.daily_limit} sent today</span>
            {sender.notes && <small>{sender.notes}</small>}
          </div>
        ))}
      </section>

      <section>
        <div className="panel-heading compact-heading">
          <h2>Recipient timelines</h2>
          <span>{recipients.length} total</span>
        </div>
        <div className="timeline-list">
          {recipients.map((recipient) => {
            const rowMessages = [...(messagesByRecipient[recipient.id] || [])].sort(
              (a, b) => a.step_number - b.step_number,
            );
            return (
              <article className="timeline-row" key={recipient.id}>
                <div className="recipient-summary">
                  <strong>{recipient.first_name} {recipient.last_name}</strong>
                  <span>{recipient.email}</span>
                  <span>{recipient.status}</span>
                </div>
                <div className="step-timeline">
                  {rowMessages.map((message) => {
                    const sender = senderById[message.sender_account_index];
                    return (
                      <div className={`timeline-step step-${message.status}`} key={message.id}>
                        <div className="step-title">
                          <span>{message.step_name}</span>
                          <strong>{message.status}</strong>
                        </div>
                        <small>Scheduled: {formatDateTime(message.scheduled_at)}</small>
                        <small>Sent: {formatDateTime(message.sent_at)}</small>
                        {message.opened_at && (
                          <small><Eye size={13} /> Opened {formatDateTime(message.opened_at)}</small>
                        )}
                        {message.replied_at && (
                          <small><Reply size={13} /> Replied {formatDateTime(message.replied_at)}</small>
                        )}
                        {sender && <small>Sender: {sender.email}</small>}
                        {message.skipped_reason && <small>Reason: {message.skipped_reason}</small>}
                        {message.error && <small className="error-text">Error: {message.error}</small>}
                      </div>
                    );
                  })}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section>
        <div className="panel-heading compact-heading">
          <h2>Recent events</h2>
          <span>{events.length}</span>
        </div>
        <div className="event-list">
          {events.length === 0 && <div className="empty-state">No events recorded yet.</div>}
          {events.map((event) => (
            <div className="event-row" key={event.id}>
              <strong>{event.event_type}</strong>
              <span>{formatDateTime(event.created_at)}</span>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

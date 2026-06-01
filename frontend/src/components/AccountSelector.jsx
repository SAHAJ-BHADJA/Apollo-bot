import { Plus, X } from 'lucide-react';
import React, { useState } from 'react';

export default function AccountSelector({ accounts, value, onChange, onAddAccount }) {
  const [showForm, setShowForm] = useState(false);
  const [accountEmail, setAccountEmail] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [emailCreditLimit, setEmailCreditLimit] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const currentAccount = accounts.find((account) => account.is_active);
  const selectedAccount =
    value === null || value === undefined
      ? currentAccount
      : accounts.find((account) => account.account_index === value);

  const emailOrFallback = (account) => account?.account_email || `Account ${account?.account_index}`;

  const optionLabel = (account) => {
    return `${emailOrFallback(account)} (${account.status})`;
  };

  const creditPercent = (account) => {
    if (!account || !account.email_credit_limit) return account?.status === 'active' ? 100 : 8;
    return Math.max(
      0,
      Math.min(100, (account.estimated_email_credits_remaining / account.email_credit_limit) * 100),
    );
  };

  const creditText = (account) => {
    if (!account) return '';
    if (account.email_credit_limit) {
      return `${account.estimated_email_credits_remaining} of ${account.email_credit_limit} estimated email credits left`;
    }
    return 'Apollo remaining email credits are not exposed here; showing local app usage';
  };

  const resetForm = () => {
    setAccountEmail('');
    setApiKey('');
    setEmailCreditLimit('');
    setNotes('');
    setFormError('');
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!onAddAccount) return;
    setFormError('');
    if (!accountEmail.trim()) {
      setFormError('Account email is required.');
      return;
    }
    if (!apiKey.trim()) {
      setFormError('Apollo API key is required.');
      return;
    }
    setSaving(true);
    try {
      await onAddAccount({
        account_email: accountEmail.trim(),
        api_key: apiKey.trim(),
        email_credit_limit: emailCreditLimit === '' ? null : Number(emailCreditLimit),
        notes: notes.trim(),
      });
      resetForm();
      setShowForm(false);
    } catch (error) {
      setFormError(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="field-group account-control">
      <div className="filter-heading">
        <label htmlFor="account">Apollo account</label>
        {onAddAccount && (
          <button className="text-button" type="button" onClick={() => setShowForm(true)}>
            <Plus size={15} />
            Add account
          </button>
        )}
      </div>
      <select
        id="account"
        value={value ?? ''}
        onChange={(event) => {
          const next = event.target.value;
          onChange(next === '' ? null : Number(next));
        }}
      >
        <option value="">Auto select and rotate if needed</option>
        {accounts.map((account) => (
          <option key={account.account_index} value={account.account_index}>
            {optionLabel(account)}
          </option>
        ))}
      </select>
      {selectedAccount && (
        <div className="account-summary">
          <span className="account-badge">
            {value === null || value === undefined ? 'Auto mode' : 'Selected account'}
          </span>
          <span>
            {value === null || value === undefined
              ? 'Auto will start with '
              : 'Only this account will be used: '}
            <strong>{emailOrFallback(selectedAccount)}</strong>
            {selectedAccount.masked_key ? ` (${selectedAccount.masked_key})` : ''}.
          </span>
          <div className="usage-meter" aria-label={creditText(selectedAccount)}>
            <div
              className={`usage-meter-fill usage-${selectedAccount.status}`}
              style={{ width: `${creditPercent(selectedAccount)}%` }}
            />
          </div>
          <div className="usage-line">{creditText(selectedAccount)}</div>
          <div className="usage-stats">
            <span>{selectedAccount.total_preview_requests || 0} previews</span>
            <span>{selectedAccount.total_email_reveal_requests || 0} CSV downloads</span>
            <span>{selectedAccount.total_verified_emails_exported || 0} verified emails exported</span>
          </div>
        </div>
      )}
      {showForm && (
        <div className="modal-backdrop" role="presentation">
          <form className="modal-card account-modal" onSubmit={handleSubmit}>
            <div className="modal-heading">
              <div>
                <h2>Add Apollo account</h2>
                <p>Saved keys are encrypted. The frontend will only show the masked key after saving.</p>
              </div>
              <button
                className="icon-button secondary-button"
                type="button"
                onClick={() => {
                  resetForm();
                  setShowForm(false);
                }}
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <section className="field-group">
              <label htmlFor="apollo-account-email">Account email</label>
              <input
                id="apollo-account-email"
                value={accountEmail}
                onChange={(event) => setAccountEmail(event.target.value)}
                placeholder="name@example.com"
              />
            </section>
            <section className="field-group">
              <label htmlFor="apollo-api-key">Apollo API key</label>
              <input
                id="apollo-api-key"
                type="password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Paste Apollo API key"
              />
            </section>
            <section className="field-group">
              <label htmlFor="apollo-credit-limit">Estimated monthly email credit limit</label>
              <input
                id="apollo-credit-limit"
                type="number"
                min="0"
                value={emailCreditLimit}
                onChange={(event) => setEmailCreditLimit(event.target.value)}
                placeholder="Optional"
              />
            </section>
            <section className="field-group">
              <label htmlFor="apollo-account-notes">Notes</label>
              <input
                id="apollo-account-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Optional"
              />
            </section>
            {formError && <div className="inline-error">{formError}</div>}
            <div className="modal-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  resetForm();
                  setShowForm(false);
                }}
              >
                Cancel
              </button>
              <button type="submit" disabled={saving}>
                <Plus size={18} />
                {saving ? 'Saving...' : 'Save account'}
              </button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

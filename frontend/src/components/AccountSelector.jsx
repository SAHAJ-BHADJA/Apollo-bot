import React from 'react';

export default function AccountSelector({ accounts, value, onChange }) {
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

  return (
    <section className="field-group account-control">
      <label htmlFor="account">Apollo account</label>
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
    </section>
  );
}

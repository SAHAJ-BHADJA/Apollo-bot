import React from 'react';

export default function StatusBox({ error, messages, health, accounts, activity }) {
  const activeAccounts = accounts.filter((account) => account.status === 'active').length;
  const limitedAccounts = accounts.filter((account) =>
    ['empty', 'rate_limited', 'failed'].includes(account.status),
  ).length;

  return (
    <aside className={`status-box ${error ? 'status-error' : ''}`}>
      {error ? <strong>{error}</strong> : <strong>{activity || 'Ready'}</strong>}
      <p>
        Backend {health?.status || 'checking'} - {accounts.length} account
        {accounts.length === 1 ? '' : 's'} configured - {activeAccounts} active - {limitedAccounts} limited/failed
      </p>
      {messages.length > 0 && (
        <ul>
          {messages.map((message, index) => (
            <li key={`${message}-${index}`}>{message}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

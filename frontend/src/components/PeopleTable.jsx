import React from 'react';

export default function PeopleTable({ people, loading }) {
  if (loading) {
    return (
      <div className="loading-state">
        <span className="spinner" />
        Searching Apollo...
      </div>
    );
  }

  if (!people.length) {
    return <div className="empty-state">No preview loaded yet.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>First Name</th>
            <th>Last Name</th>
            <th>Title</th>
            <th>Company</th>
            <th>LinkedIn</th>
          </tr>
        </thead>
        <tbody>
          {people.map((person, index) => (
            <tr key={`${person.apollo_person_id}-${index}`}>
              <td>{person.first_name}</td>
              <td>{person.last_name}</td>
              <td>{person.title}</td>
              <td>{person.company}</td>
              <td>
                {person.linkedin_url ? (
                  <a href={person.linkedin_url} target="_blank" rel="noreferrer">
                    Profile
                  </a>
                ) : (
                  <span className="muted">None</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

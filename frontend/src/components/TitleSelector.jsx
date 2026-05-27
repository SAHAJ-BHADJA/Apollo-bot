import { Plus } from 'lucide-react';
import React from 'react';
import { useState } from 'react';

export const DEFAULT_TITLES = [
  'HR',
  'Recruiter',
  'University Recruiter',
  'Senior Software Engineer',
  'Software Engineer',
  'Talent Acquisition Specialist',
  'Talent Acquisition',
  'Talent Acquisition Manager',
];

export default function TitleSelector({ selectedTitles, onChange }) {
  const [customTitle, setCustomTitle] = useState('');

  const toggleTitle = (title) => {
    if (selectedTitles.includes(title)) {
      onChange(selectedTitles.filter((item) => item !== title));
    } else {
      onChange([...selectedTitles, title]);
    }
  };

  const addCustomTitle = () => {
    const nextTitle = customTitle.trim();
    if (!nextTitle || selectedTitles.includes(nextTitle)) return;
    onChange([...selectedTitles, nextTitle]);
    setCustomTitle('');
  };

  const customSelectedTitles = selectedTitles.filter((title) => !DEFAULT_TITLES.includes(title));

  return (
    <section className="field-group">
      <label>Job titles</label>
      <div className="title-grid">
        {DEFAULT_TITLES.map((title) => (
          <label className="check-row" key={title}>
            <input
              type="checkbox"
              checked={selectedTitles.includes(title)}
              onChange={() => toggleTitle(title)}
            />
            <span>{title}</span>
          </label>
        ))}
      </div>

      {customSelectedTitles.length > 0 && (
        <div className="chip-row">
          {customSelectedTitles.map((title) => (
            <button className="chip" key={title} type="button" onClick={() => toggleTitle(title)}>
              {title}
            </button>
          ))}
        </div>
      )}

      <div className="inline-row">
        <input
          value={customTitle}
          onChange={(event) => setCustomTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              addCustomTitle();
            }
          }}
          placeholder="Add custom title"
        />
        <button className="icon-button" type="button" onClick={addCustomTitle} title="Add title">
          <Plus size={18} />
        </button>
      </div>
    </section>
  );
}

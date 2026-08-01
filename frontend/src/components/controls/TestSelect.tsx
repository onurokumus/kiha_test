import { CSSProperties, useMemo } from 'react';
import { TestInfo } from '../../types';
import { SearchableSelect, SearchableSelectOption } from './SearchableSelect';

interface TestSelectProps {
  tests: TestInfo[];
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  className?: string;
  style?: CSSProperties;
  size?: 'compact' | 'default';
}

function formatTestDescription(test: TestInfo): string {
  if (test.status !== 'ready') {
    return test.error ? `${test.status} - ${test.error}` : test.status;
  }

  const details: string[] = [];
  if (test.n_rows != null) details.push(`${test.n_rows.toLocaleString()} rows`);
  if (test.fs_hz != null) details.push(`${test.fs_hz.toLocaleString()} Hz`);
  if (test.duration_s != null) details.push(`${test.duration_s.toLocaleString()} s`);
  return details.join(' - ') || 'Ready';
}

export const TestSelect = ({
  tests,
  value,
  onChange,
  ariaLabel,
  className,
  style,
  size,
}: TestSelectProps) => {
  const options = useMemo<SearchableSelectOption[]>(
    () =>
      tests.map((test) => ({
        value: test.name,
        label: test.name,
        description: formatTestDescription(test),
        keywords: [test.status, test.source_file ?? ''],
        group: test.status === 'ready' ? 'Ready tests' : 'Unavailable',
        disabled: test.status !== 'ready',
      })),
    [tests]
  );

  return (
    <SearchableSelect
      value={value}
      options={options}
      onChange={onChange}
      ariaLabel={ariaLabel}
      className={className}
      style={style}
      size={size}
      optionNoun="test"
      searchPlaceholder="Search tests..."
      emptyMessage="No matching tests"
      menuMinWidth={320}
    />
  );
};

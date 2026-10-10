import { useEffect, useRef, useState } from 'react';
import { CalendarClock, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  dateTimeProblem,
  displayLocalDateTime,
  parseLocalDateTime,
} from '@/lib/forms';

// English-only replacements for <input type="file"> and
// <input type="datetime-local">, whose native UI follows the OS language.

export function FilePicker({
  accept,
  disabled,
  label = 'Choose file',
  fileName,
  onFile,
}: {
  accept: string;
  disabled?: boolean;
  label?: string;
  /** Shown next to the button when defined; '' shows "No file chosen". */
  fileName?: string;
  onFile: (file: File | null) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <span className="file-picker">
      <input
        ref={ref}
        type="file"
        hidden
        accept={accept}
        disabled={disabled}
        onChange={(e) => {
          onFile(e.target.files?.[0] ?? null);
          e.target.value = '';
        }}
      />
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={() => ref.current?.click()}
      >
        <Upload size={16} />
        {label}
      </Button>
      {fileName !== undefined && (
        <span className="muted">{fileName || 'No file chosen'}</span>
      )}
    </span>
  );
}

/** Text field for local date-times; value/onChange use the datetime-local format. */
export function DateTimeInput({
  value,
  onChange,
  min,
  max,
  required,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(displayLocalDateTime(value));
  // Follow external changes (e.g. replay) unless the draft already means this value.
  const [synced, setSynced] = useState(value);
  if (value !== synced) {
    setSynced(value);
    if (parseLocalDateTime(draft) !== value)
      setDraft(displayLocalDateTime(value));
  }
  const problem = dateTimeProblem(draft, { required, min, max });
  useEffect(() => {
    ref.current?.setCustomValidity(problem);
  }, [problem]);
  return (
    <span className="date-time-input">
      <Input
        ref={ref}
        data-own-validity=""
        aria-invalid={problem !== '' && draft !== '' ? true : undefined}
        title={problem || undefined}
        inputMode="numeric"
        placeholder="YYYY-MM-DD HH:MM"
        required={required}
        disabled={disabled}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          const parsed = parseLocalDateTime(e.target.value);
          if (parsed) onChange(parsed);
        }}
      />
      <CalendarClock size={15} aria-hidden="true" />
    </span>
  );
}

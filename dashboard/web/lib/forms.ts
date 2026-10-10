// Browser-native form widgets (file pickers, date-time pickers, validation
// bubbles) are drawn in the operating-system language, not the page language.
// These helpers let the dashboard replace them with English-only equivalents.

const LOCAL_DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/;

/** Parse "YYYY-MM-DD HH:MM" (or the "T" form) into the datetime-local value format, or null. */
export function parseLocalDateTime(text: string): string | null {
  const m = LOCAL_DATE_TIME.exec(text.trim());
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  const date = new Date(y, mo - 1, d, h, mi);
  if (
    date.getFullYear() !== y ||
    date.getMonth() !== mo - 1 ||
    date.getDate() !== d ||
    h > 23 ||
    mi > 59
  )
    return null;
  return `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}`;
}

export const displayLocalDateTime = (value: string) => value.replace('T', ' ');

/** English validity message for a date-time text field; '' when valid. */
export function dateTimeProblem(
  text: string,
  { required, min, max }: { required?: boolean; min?: string; max?: string },
): string {
  if (!text.trim()) return required ? 'Please enter a date and time.' : '';
  const value = parseLocalDateTime(text);
  if (!value) return 'Use the format YYYY-MM-DD HH:MM (24-hour clock).';
  if (min && value < min)
    return `Must be ${displayLocalDateTime(min)} or later.`;
  if (max && value > max)
    return `Must be ${displayLocalDateTime(max)} or earlier.`;
  return '';
}

type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function englishMessage(el: Field): string {
  const v = el.validity;
  const input = el instanceof HTMLInputElement ? el : null;
  if (v.valueMissing) {
    if (input?.type === 'file') return 'Please choose a file.';
    if (el instanceof HTMLSelectElement) return 'Please select an item in the list.';
    return 'Please fill in this field.';
  }
  if (v.badInput) return input?.type === 'number' ? 'Please enter a number.' : 'Please enter a valid value.';
  if (v.patternMismatch)
    return el.dataset.patternMessage || 'Please match the requested format.';
  if (v.typeMismatch) return 'Please enter a valid value.';
  if (v.tooLong && input) return `Please use at most ${input.maxLength} characters.`;
  if (v.tooShort && input) return `Please use at least ${input.minLength} characters.`;
  if (v.rangeUnderflow && input) return `Value must be ${input.min} or more.`;
  if (v.rangeOverflow && input) return `Value must be ${input.max} or less.`;
  if (v.stepMismatch) return 'Please enter a valid value.';
  return '';
}

const isField = (t: EventTarget | null): t is Field =>
  t instanceof HTMLInputElement ||
  t instanceof HTMLSelectElement ||
  t instanceof HTMLTextAreaElement;

/** Replace the browser's localized validation bubbles with English text. */
export function installEnglishValidation(doc: Document = document) {
  // Fields that manage their own custom validity opt out with data-own-validity.
  doc.addEventListener(
    'invalid',
    (e) => {
      const el = e.target;
      if (!isField(el) || 'ownValidity' in el.dataset) return;
      el.setCustomValidity('');
      el.setCustomValidity(englishMessage(el));
    },
    true,
  );
  const clear = (e: Event) => {
    const el = e.target;
    if (isField(el) && !('ownValidity' in el.dataset)) el.setCustomValidity('');
  };
  doc.addEventListener('input', clear, true);
  doc.addEventListener('change', clear, true);
}

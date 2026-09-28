import { forwardRef, useId, useRef, type InputHTMLAttributes } from 'react';
import { numericError, type NumericConstraints } from '../../utils/numericField';
import styles from './NumericField.module.css';

export interface NumericFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>, NumericConstraints {
  unit?: string;
  /** A cross-field constraint, shown once the ordinary numeric rules pass. */
  error?: string;
}

/** Raw text belongs to the caller: editing never converts an empty draft into zero.
 * Select on entry, while allowing a second click to place the caret for corrections. */
export const NumericField = forwardRef<HTMLInputElement, NumericFieldProps>(function NumericField({
  unit, error, min, max, exclusiveMin, exclusiveMax, integer, allowEmpty,
  disabled, readOnly, value, className = '', style, onFocus, onMouseDown, onMouseUp,
  'aria-describedby': describedBy, 'aria-invalid': suppliedInvalid, ...props
}, ref) {
  const id = useId();
  const selectOnMouseUp = useRef(false);
  const problem = disabled || readOnly ? '' : numericError(value, {
    min, max, exclusiveMin, exclusiveMax, integer, allowEmpty,
  }) || error || '';
  const invalid = !!problem || suppliedInvalid === true || suppliedInvalid === 'true';
  return (
    <span className={styles.field} style={style?.width !== undefined ? { width: style.width } : undefined}>
      <span className={styles.control} data-invalid={invalid || undefined} data-disabled={disabled || undefined}>
        <input
          {...props}
          ref={ref}
          type="text"
          inputMode={props.inputMode ?? (integer ? 'numeric' : 'decimal')}
          value={value}
          disabled={disabled}
          readOnly={readOnly}
          className={`${className} ${styles.input}`}
          style={style ? { ...style, width: '100%' } : undefined}
          aria-invalid={invalid || undefined}
          aria-describedby={[describedBy, problem ? `${id}-warning` : null].filter(Boolean).join(' ') || undefined}
          onMouseDown={(event) => {
            selectOnMouseUp.current = document.activeElement !== event.currentTarget;
            onMouseDown?.(event);
          }}
          onFocus={(event) => {
            event.currentTarget.select();
            onFocus?.(event);
          }}
          onMouseUp={(event) => {
            if (selectOnMouseUp.current) {
              event.preventDefault();
              event.currentTarget.select();
              selectOnMouseUp.current = false;
            }
            onMouseUp?.(event);
          }}
        />
        {unit && <span className={styles.unit} aria-hidden="true">{unit}</span>}
      </span>
      {problem && <span id={`${id}-warning`} className={styles.warning} aria-live="polite">{problem}</span>}
    </span>
  );
});

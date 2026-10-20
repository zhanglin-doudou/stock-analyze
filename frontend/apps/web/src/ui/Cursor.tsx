export interface CursorProps {
  hasMore: boolean;
  nextCursor: string | null;
  onNext: () => void;
  onReset?: () => void;
  disabled?: boolean;
}

/** 游标分页控件：只支持向后翻页，或回到第一页。 */
export function Cursor({ hasMore, nextCursor, onNext, onReset, disabled }: CursorProps) {
  return (
    <div className="cursor">
      <button className="btn" type="button" disabled={disabled || !hasMore || !nextCursor} onClick={onNext}>
        下一页
      </button>
      {onReset ? (
        <button className="btn btn-ghost" type="button" disabled={disabled} onClick={onReset}>
          回到第一页
        </button>
      ) : null}
    </div>
  );
}

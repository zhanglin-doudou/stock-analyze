import { useRef, useState } from "react";

export interface UploaderProps {
  accept?: string;
  disabled?: boolean;
  onFile: (file: File) => void;
}

export function Uploader({ accept = ".csv,.xls,.xlsx", disabled, onFile }: UploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");

  return (
    <div className="uploader">
      <input
        ref={inputRef}
        className="uploader-input"
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setName(file.name);
            onFile(file);
          }
        }}
      />
      <button
        className="uploader-btn"
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
      >
        选择账单文件
      </button>
      <span className="uploader-name">
        {name ? `已选择：${name}` : "支持 .xls/.xlsx/.csv，≤20MB"}
      </span>
    </div>
  );
}

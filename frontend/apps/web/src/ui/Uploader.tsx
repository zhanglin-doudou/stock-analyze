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
      <span className="muted">{name ? `已选择：${name}` : "支持 .xls/.xlsx/.csv，≤20MB"}</span>
    </div>
  );
}

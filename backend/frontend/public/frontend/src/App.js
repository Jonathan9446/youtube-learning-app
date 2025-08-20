// frontend/src/App.js
import React, { useState, useEffect, useRef } from 'react';

function App() {
  const [url, setUrl] = useState('');
  const [taskId, setTaskId] = useState(null);
  const [sentences, setSentences] = useState([]);
  const [lastKey, setLastKey] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState(0);
  const playerRef = useRef(null);

  const extractVideoId = (url) => {
    const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|&v=)([^#&?]*).*/;
    const match = url.match(regExp);
    return (match && match[2].length === 11) ? match[2] : null;
  };

  const startProcessing = async () => {
    if (!url) return;
    
    setIsProcessing(true);
    setSentences([]);
    setLastKey(null);
    
    try {
      const response = await fetch('/api/start_processing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      
      const data = await response.json();
      setTaskId(data.task_id);
    } catch (error) {
      console.error('Failed to start processing:', error);
      setIsProcessing(false);
    }
  };

  useEffect(() => {
    if (!taskId) return;
    
    const pollInterval = setInterval(async () => {
      try {
        const statusRes = await fetch(`/api/task_status/${taskId}`);
        const status = await statusRes.json();
        
        if (status.total_chunks > 0) {
          const newProgress = Math.round((status.processed_chunks / status.total_chunks) * 100);
          setProgress(newProgress);
        }
        
        const transcriptUrl = lastKey 
          ? `/api/transcript/${taskId}?last_key=${lastKey}`
          : `/api/transcript/${taskId}`;
          
        const transcriptRes = await fetch(transcriptUrl);
        const data = await transcriptRes.json();
        
        if (data.sentences && data.sentences.length > 0) {
          const startNum = sentences.length + 1;
          const newSentences = data.sentences.map((sentence, i) => ({
            ...sentence,
            sentence_number: startNum + i
          }));
          
          setSentences(prev => [...prev, ...newSentences]);
          setLastKey(data.last_key);
        }
        
        if (status.status === 'completed') {
          clearInterval(pollInterval);
          setIsProcessing(false);
        }
      } catch (error) {
        console.error('Polling error:', error);
      }
    }, 2500);
    
    return () => clearInterval(pollInterval);
  }, [taskId, lastKey, sentences.length]);

  const seekToTime = (timeStr) => {
    if (!playerRef.current) return;
    
    const [hours, minutes, seconds] = timeStr.split(':').map(Number);
    const totalSeconds = hours * 3600 + minutes * 60 + seconds;
    
    playerRef.current.seekTo(totalSeconds, true);
  };

  const videoId = extractVideoId(url);

  return (
    <div className="container">
      <h1>YouTube Learning Assistant</h1>
      
      <div className="input-group">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Enter YouTube URL"
          disabled={isProcessing}
        />
        <button 
          onClick={startProcessing} 
          disabled={isProcessing || !url}
        >
          {isProcessing ? 'Processing...' : 'Start'}
        </button>
      </div>
      
      {isProcessing && (
        <div className="progress-bar">
          <div className="progress" style={{ width: `${progress}%` }}></div>
          <div className="progress-text">{progress}%</div>
        </div>
      )}
      
      {videoId && (
        <div className="video-container">
          <iframe
            ref={playerRef}
            src={`https://www.youtube.com/embed/${videoId}`}
            title="YouTube video player"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          ></iframe>
        </div>
      )}
      
      {sentences.length > 0 && (
        <div className="transcript-container">
          <h2>Interactive Transcript</h2>
          <div className="sentences-list">
            {sentences.map((sentence) => (
              <div 
                key={sentence.key}
                className="sentence-card"
                onClick={() => seekToTime(sentence.start_time)}
              >
                <div className="sentence-header">
                  <span className="sentence-number">#{sentence.sentence_number}</span>
                  <span className="timestamp">{sentence.start_time}</span>
                </div>
                <div className="english">{sentence.english}</div>
                <div className="hindi-pronunciation">{sentence.pronunciation_hindi}</div>
                <div className="hindi-translation">{sentence.translation_hindi}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;

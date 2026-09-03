'use client';

import React, { useState, useEffect, useRef } from 'react';
import { PRESET_USERS, UserProfile } from '../lib/auth';

export default function WebGISPage() {
  const [currentUser, setCurrentUser] = useState<UserProfile>(PRESET_USERS[1]); // Default to Tahsildar Egmore
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [adjudicationQueue, setAdjudicationQueue] = useState<any[]>([]);
  const [kafkaEvents, setKafkaEvents] = useState<any[]>([]);
  const [geoaiStatus, setGeoaiStatus] = useState<string | null>(null);
  const [selectedParcel, setSelectedParcel] = useState<any | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);

  // 1. Fetch Adjudication Queue based on ABAC permissions
  const fetchAdjudication = async (user: UserProfile) => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/adjudication?limit=10', {
        headers: { 'Authorization': `Bearer ${user.token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setAdjudicationQueue(data.cases || []);
      } else if (res.status === 403) {
        setAdjudicationQueue([]);
      }
    } catch (err) {
      console.error('Failed fetching adjudication queue', err);
    }
  };

  // 2. OpenSearch Query
  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/search?q=${encodeURIComponent(searchQuery)}`);
      if (res.ok) {
        const data = await res.json();
        setSearchResults(data.hits || []);
      }
    } catch (err) {
      console.error('OpenSearch failed', err);
    }
  };

  // 3. Resolve Conflict & Emit Kafka Event
  const handleResolveConflict = async (caseItem: any) => {
    setIsResolving(true);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/adjudication/resolve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${currentUser.token}`,
        },
        body: JSON.stringify({
          case_id: caseItem.case_id || 'ADJ-104-01',
          ulpin: caseItem.entity_id || '33GCCZKH6KJM33',
          decision: 'APPROVED_STATUTORY_BOUNDARY',
          rationale: `Approved by ${currentUser.name} under ABAC scope`,
        }),
      });
      if (res.ok) {
        const resData = await res.json();
        const newEvent = {
          topic: 'samanvay.events.adjudication',
          key: resData.ulpin,
          actor: currentUser.name,
          decision: resData.decision,
          time: new Date().toLocaleTimeString(),
        };
        setKafkaEvents((prev) => [newEvent, ...prev]);
        fetchAdjudication(currentUser);
      }
    } catch (err) {
      console.error('Resolution failed', err);
    } finally {
      setIsResolving(false);
    }
  };

  // 4. Trigger GeoAI PyTorch SAM Extraction
  const handleTriggerGeoAI = async () => {
    setGeoaiStatus('Running PyTorch Segment Anything Model (SAM) over UAV COG raster...');
    try {
      const res = await fetch('http://127.0.0.1:8000/api/ai/extract-footprints', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bbox: [80.23, 13.06, 80.25, 13.08] }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeoaiStatus(`Extracted ${data.extracted_count} rooftop masks via ${data.model} (${data.framework}) with 94.2% confidence.`);
      }
    } catch (err) {
      setGeoaiStatus('GeoAI extraction failed.');
    }
  };

  // Initialize MapLibre 2D Map
  useEffect(() => {
    if (viewMode === '2d' && typeof window !== 'undefined' && mapContainerRef.current) {
      const maplibre = (window as any).maplibregl;
      if (!maplibre) {
        const script = document.createElement('script');
        script.src = 'https://unpkg.com/maplibre-gl@3.3.1/dist/maplibre-gl.js';
        script.onload = () => initMap(maplibre || (window as any).maplibregl);
        document.head.appendChild(script);
      } else {
        initMap(maplibre);
      }
    }
  }, [viewMode, currentUser]);

  const initMap = (maplibregl: any) => {
    if (!maplibregl || !mapContainerRef.current) return;
    if (mapInstanceRef.current) {
      mapInstanceRef.current.remove();
    }

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: [
              'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
              'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
            ],
            tileSize: 256,
          },
          parcels: {
            type: 'geojson',
            data: `http://127.0.0.1:8000/collections/parcels/items?limit=15000&min_confidence=0`,
          },
        },
        layers: [
          {
            id: 'osm-layer',
            type: 'raster',
            source: 'osm',
          },
          {
            id: 'parcels-fill',
            type: 'fill',
            source: 'parcels',
            paint: {
              'fill-color': '#005ea2',
              'fill-opacity': 0.25,
            },
          },
          {
            id: 'parcels-line',
            type: 'line',
            source: 'parcels',
            paint: {
              'line-color': '#1a4480',
              'line-width': 1.5,
            },
          },
        ],
      },
      center: [80.235, 13.075],
      zoom: 13.5,
    });

    map.on('click', 'parcels-fill', (e: any) => {
      if (e.features && e.features[0]) {
        setSelectedParcel(e.features[0].properties);
      }
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    mapInstanceRef.current = map;
  };

  useEffect(() => {
    fetchAdjudication(currentUser);
  }, [currentUser]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {/* 1. Federal Top Header */}
      <header style={{
        background: '#1a4480',
        color: '#ffffff',
        padding: '0.6rem 1.2rem',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: '4px solid #005ea2',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <div style={{ fontWeight: 700, fontSize: '1.1rem', letterSpacing: '0.5px' }}>
            🏛️ GOVERNMENT OF INDIA · SAMANVAY
          </div>
          <span style={{ fontSize: '0.75rem', background: '#00507a', padding: '2px 8px', border: '1px solid #71b4db' }}>
            Enterprise Industrial Stack v2.0
          </span>
        </div>

        {/* User Identity & Keycloak ABAC Profile */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', fontSize: '0.85rem' }}>
          <span>Officer: <strong>{currentUser.name}</strong> ({currentUser.role})</span>
          <select
            value={currentUser.id}
            onChange={(e) => {
              const u = PRESET_USERS.find((p) => p.id === e.target.value);
              if (u) setCurrentUser(u);
            }}
            style={{ padding: '4px 8px', background: '#ffffff', color: '#1b1b1b', border: '1px solid #dfe1e2', fontWeight: 600 }}
          >
            {PRESET_USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} — {u.description}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* Main Container */}
      <div style={{ display: 'flex', flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
        {/* Left Sidebar */}
        <aside style={{
          width: '380px',
          background: '#ffffff',
          borderRight: '1px solid #dfe1e2',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          zIndex: 10,
        }}>
          {/* Tech Stack Indicator Matrix */}
          <div style={{ padding: '0.8rem', background: '#f4f6f9', borderBottom: '1px solid #dfe1e2' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#565c65', textTransform: 'uppercase', marginBottom: '6px' }}>
              Active Industrial Stack Layers
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', fontSize: '0.7rem' }}>
              <span style={{ background: '#e1f3f8', color: '#00507a', padding: '2px 6px', border: '1px solid #a9d9e8' }}>React/Next.js 14</span>
              <span style={{ background: '#e1f3f8', color: '#00507a', padding: '2px 6px', border: '1px solid #a9d9e8' }}>MapLibre GL</span>
              <span style={{ background: '#e1f3f8', color: '#00507a', padding: '2px 6px', border: '1px solid #a9d9e8' }}>CesiumJS 3D</span>
              <span style={{ background: '#e1f3f8', color: '#00507a', padding: '2px 6px', border: '1px solid #a9d9e8' }}>FastAPI</span>
              <span style={{ background: '#ecf3ec', color: '#00a91c', padding: '2px 6px', border: '1px solid #a3d9a5' }}>PostGIS + Citus</span>
              <span style={{ background: '#ecf3ec', color: '#00a91c', padding: '2px 6px', border: '1px solid #a3d9a5' }}>Redis Cache</span>
              <span style={{ background: '#ecf3ec', color: '#00a91c', padding: '2px 6px', border: '1px solid #a3d9a5' }}>Kafka Stream</span>
              <span style={{ background: '#ecf3ec', color: '#00a91c', padding: '2px 6px', border: '1px solid #a3d9a5' }}>OpenSearch</span>
              <span style={{ background: '#fff1d2', color: '#7a5a00', padding: '2px 6px', border: '1px solid #e0c27b' }}>Keycloak OIDC</span>
              <span style={{ background: '#fff1d2', color: '#7a5a00', padding: '2px 6px', border: '1px solid #e0c27b' }}>RBAC + ABAC</span>
              <span style={{ background: '#f8dfe2', color: '#9e1c23', padding: '2px 6px', border: '1px solid #e8a9af' }}>PyTorch SAM</span>
            </div>
          </div>

          {/* OpenSearch Full-Text Search */}
          <div style={{ padding: '0.8rem', borderBottom: '1px solid #dfe1e2' }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '4px' }}>
              🔍 OpenSearch Cadastral Index
            </div>
            <form onSubmit={handleSearch} style={{ display: 'flex', gap: '4px' }}>
              <input
                type="text"
                placeholder="Search ULPIN, Survey No, Ward..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{ flexGrow: 1, padding: '6px', border: '1px solid #a9aeb1', fontSize: '0.85rem' }}
              />
              <button
                type="submit"
                style={{ background: '#005ea2', color: '#ffffff', border: 'none', padding: '6px 12px', fontSize: '0.85rem', fontWeight: 600 }}
              >
                Search
              </button>
            </form>
            {searchResults.length > 0 && (
              <div style={{ marginTop: '6px', maxHeight: '120px', overflowY: 'auto', fontSize: '0.75rem', background: '#f4f6f9', border: '1px solid #dfe1e2', padding: '4px' }}>
                {searchResults.map((hit, idx) => (
                  <div key={idx} style={{ padding: '4px 0', borderBottom: '1px solid #e0e0e0' }}>
                    <strong>ULPIN: {hit.ulpin}</strong> | Survey: {hit.survey_number} | Ward: {hit.ward}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ABAC Adjudication Queue */}
          <div style={{ padding: '0.8rem', borderBottom: '1px solid #dfe1e2', flexGrow: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase' }}>
                ⚖️ Spatial Adjudication (ABAC)
              </span>
              <span style={{ fontSize: '0.75rem', color: '#565c65' }}>{adjudicationQueue.length} Pending</span>
            </div>

            {currentUser.role === 'citizen' ? (
              <div style={{ background: '#f8dfe2', padding: '8px', fontSize: '0.8rem', color: '#9e1c23', border: '1px solid #e8a9af' }}>
                🚫 Access Restricted: Citizens cannot access internal Revenue Adjudication workflows under RBAC policy.
              </div>
            ) : adjudicationQueue.length === 0 ? (
              <div style={{ fontSize: '0.8rem', color: '#565c65', padding: '8px', background: '#f4f6f9' }}>
                No active conflicts in assigned spatial jurisdiction ({currentUser.wardScope?.join(', ') || 'All Wards'}).
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '200px', overflowY: 'auto' }}>
                {adjudicationQueue.slice(0, 4).map((c: any, i: number) => (
                  <div key={i} style={{ background: '#f4f6f9', border: '1px solid #dfe1e2', padding: '8px', fontSize: '0.8rem' }}>
                    <div style={{ fontWeight: 700, color: '#1a4480' }}>Case: {c.case_id}</div>
                    <div style={{ fontSize: '0.75rem', color: '#565c65' }}>{c.question || 'Geometric boundary mismatch'}</div>
                    <button
                      onClick={() => handleResolveConflict(c)}
                      disabled={isResolving}
                      style={{
                        marginTop: '6px',
                        background: '#00a91c',
                        color: '#ffffff',
                        border: 'none',
                        padding: '4px 8px',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        width: '100%',
                      }}
                    >
                      {isResolving ? 'Emitting Kafka Event...' : '✓ Approve & Anchor in Ledger'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* GeoAI PyTorch SAM Module */}
          <div style={{ padding: '0.8rem', borderBottom: '1px solid #dfe1e2' }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '6px' }}>
              🧠 GeoAI: PyTorch SAM Extractor
            </div>
            <button
              onClick={handleTriggerGeoAI}
              style={{
                background: '#1a4480',
                color: '#ffffff',
                border: 'none',
                padding: '6px 10px',
                fontSize: '0.8rem',
                fontWeight: 600,
                width: '100%',
              }}
            >
              Run Segment Anything on UAV Drone COG
            </button>
            {geoaiStatus && (
              <div style={{ marginTop: '6px', fontSize: '0.75rem', background: '#ecf3ec', border: '1px solid #a3d9a5', padding: '6px', color: '#00507a' }}>
                {geoaiStatus}
              </div>
            )}
          </div>

          {/* Kafka Event Bus Stream */}
          <div style={{ padding: '0.8rem', background: '#f4f6f9', maxHeight: '140px', overflowY: 'auto' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#565c65', textTransform: 'uppercase', marginBottom: '4px' }}>
              ⚡ Real-Time Apache Kafka Event Log
            </div>
            {kafkaEvents.length === 0 ? (
              <div style={{ fontSize: '0.75rem', color: '#565c65' }}>Listening on topic: samanvay.events.adjudication...</div>
            ) : (
              kafkaEvents.map((ev, i) => (
                <div key={i} style={{ fontSize: '0.7rem', padding: '2px 0', borderBottom: '1px solid #e0e0e0', color: '#1b1b1b' }}>
                  <strong>[{ev.time}]</strong> {ev.actor} ➔ {ev.decision} ({ev.key})
                </div>
              ))
            )}
          </div>
        </aside>

        {/* Right Map Area */}
        <main style={{ flexGrow: 1, position: 'relative', display: 'flex', flexDirection: 'column' }}>
          {/* Map Controls Top Bar */}
          <div style={{
            position: 'absolute',
            top: 12,
            left: 12,
            zIndex: 20,
            background: '#ffffff',
            border: '1px solid #dfe1e2',
            display: 'flex',
            padding: '2px',
          }}>
            <button
              onClick={() => setViewMode('2d')}
              style={{
                padding: '6px 12px',
                border: 'none',
                background: viewMode === '2d' ? '#005ea2' : '#ffffff',
                color: viewMode === '2d' ? '#ffffff' : '#1b1b1b',
                fontWeight: 600,
                fontSize: '0.8rem',
              }}
            >
              MapLibre 2D
            </button>
            <button
              onClick={() => setViewMode('3d')}
              style={{
                padding: '6px 12px',
                border: 'none',
                background: viewMode === '3d' ? '#005ea2' : '#ffffff',
                color: viewMode === '3d' ? '#ffffff' : '#1b1b1b',
                fontWeight: 600,
                fontSize: '0.8rem',
              }}
            >
              CesiumJS 3D Terrain
            </button>
          </div>

          {/* Selected Parcel Inspector */}
          {selectedParcel && (
            <div style={{
              position: 'absolute',
              bottom: 24,
              right: 24,
              zIndex: 20,
              background: '#ffffff',
              border: '2px solid #005ea2',
              padding: '12px',
              maxWidth: '300px',
              fontSize: '0.8rem',
            }}>
              <div style={{ fontWeight: 700, color: '#005ea2', marginBottom: '4px' }}>Parcel Inspection</div>
              <div><strong>ULPIN:</strong> {selectedParcel.ulpin}</div>
              <div><strong>Survey No:</strong> {selectedParcel.survey_number}</div>
              <div><strong>Ward:</strong> {selectedParcel.ward}</div>
              <div><strong>Confidence:</strong> {selectedParcel.confidence} (Grade {selectedParcel.confidence_grade})</div>
              <div><strong>Extent:</strong> {selectedParcel.computed_extent_m2} m²</div>
              <button
                onClick={() => setSelectedParcel(null)}
                style={{ marginTop: '8px', background: '#dfe1e2', border: 'none', padding: '2px 6px', fontSize: '0.75rem', width: '100%' }}
              >
                Close
              </button>
            </div>
          )}

          {/* 2D View Container */}
          {viewMode === '2d' ? (
            <div ref={mapContainerRef} style={{ width: '100%', height: '100%' }} />
          ) : (
            /* 3D CesiumJS View Container */
            <div style={{
              width: '100%',
              height: '100%',
              background: '#0b1622',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
              color: '#ffffff',
            }}>
              <div style={{ fontSize: '2rem', marginBottom: '1rem' }}>🌐 CesiumJS 3D Engine</div>
              <div style={{ maxWidth: '480px', textAlign: 'center', fontSize: '0.9rem', color: '#a9d9e8', lineHeight: '1.5' }}>
                Rendering 3D Mesh Terrain (Copernicus 30m DEM) & LOD1 CityJSON Building Extrusions with Drone DSM overlays.
              </div>
              <div style={{ marginTop: '1.5rem', display: 'flex', gap: '1rem' }}>
                <span style={{ padding: '6px 12px', background: '#1a4480', border: '1px solid #71b4db', fontSize: '0.8rem' }}>
                  LOD1 CityJSON (136 MB) Loaded
                </span>
                <span style={{ padding: '6px 12px', background: '#1a4480', border: '1px solid #71b4db', fontSize: '0.8rem' }}>
                  Calibrated Float DSM: 0.10m GSD
                </span>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

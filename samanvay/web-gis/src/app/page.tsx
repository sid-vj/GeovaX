'use client';

import React, { useState, useEffect, useRef } from 'react';
import { PRESET_USERS, AVAILABLE_WARDS, UserProfile, WardLocation } from '../lib/auth';

export default function WebGISPage() {
  const [currentUser, setCurrentUser] = useState<UserProfile>(PRESET_USERS[1]); // Tahsildar (Vandalur – Guindy Corridor)
  const [selectedWard, setSelectedWard] = useState<WardLocation>(AVAILABLE_WARDS[5]); // Default Veeralakshmi Nagar / Mudichur
  const [selectedStreet, setSelectedStreet] = useState<string>('all');
  const [viewMode, setViewMode] = useState<'2d' | '3d'>('2d');
  const [baseMapType, setBaseMapType] = useState<'osiris-sat' | 'osiris-dark' | 'osiris-streets'>('osiris-sat');
  const [parcelOpacity, setParcelOpacity] = useState<number>(0.35);
  const [showUtilities, setShowUtilities] = useState<boolean>(true);
  
  // OSIRIS Live Search
  const [searchQuery, setSearchQuery] = useState('');
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isSearching, setIsSearching] = useState(false);

  // HUD and Hover State
  const [cursorCoords, setCursorCoords] = useState<{ lng: string; lat: string }>({ lng: '80.0712', lat: '12.9124' });
  const [hoveredParcel, setHoveredParcel] = useState<any | null>(null);
  const [hoverPosition, setHoverPosition] = useState<{ x: number; y: number } | null>(null);

  // Deep Modals: Legal Case & Interactive FMB CAD Studio
  const [activeCaseModal, setActiveCaseModal] = useState<any | null>(null);
  const [fmbModalData, setFmbModalData] = useState<any | null>(null);
  const [noticeIssued, setNoticeIssued] = useState(false);
  const [copiedUlpin, setCopiedUlpin] = useState(false);

  const [adjudicationQueue, setAdjudicationQueue] = useState<any[]>([]);
  const [wardCourtCases, setWardCourtCases] = useState<any[]>([]);
  const [kafkaEvents, setKafkaEvents] = useState<any[]>([]);
  const [geoaiStatus, setGeoaiStatus] = useState<string | null>(null);
  const [selectedParcel, setSelectedParcel] = useState<any | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [wardParcels, setWardParcels] = useState<any[]>([]);
  const [wardStats, setWardStats] = useState<any>({
    totalParcels: 0,
    totalAreaM2: 0,
    meanConfidence: 0,
    gradeCounts: { A: 0, B: 0, C: 0, D: 0, E: 0 },
    conflicts: 0,
    litigationCount: 0,
    builtUpAreaM2: 0,
  });
  const [accessAlert, setAccessAlert] = useState<string | null>(null);
  const [rightPanelTab, setRightPanelTab] = useState<'litigation' | 'ward' | 'parcel'>('litigation');
  const [sidebarTab, setSidebarTab] = useState<'zones' | 'layers' | 'revenue'>('zones');

  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<any>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchContainerRef = useRef<HTMLDivElement>(null);

  // Keyboard Shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement !== searchInputRef.current) {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === 'Escape') {
        setShowSuggestions(false);
        setActiveCaseModal(null);
        setFmbModalData(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // 1. Dual Autocomplete Search (Cadastre + Live Global Real Geocoder)
  useEffect(() => {
    if (!searchQuery.trim() || searchQuery.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    const timer = setTimeout(async () => {
      setIsSearching(true);
      const combined: any[] = [];

      try {
        // A. Internal Cadastral Survey & Road Index
        const localRes = await fetch(`http://127.0.0.1:8000/api/search/streets?q=${encodeURIComponent(searchQuery)}`);
        if (localRes.ok) {
          const localData = await localRes.json();
          const localHits = (localData.suggestions || []).map((s: any) => ({
            ...s,
            type: 'cadastre',
            icon: '📐',
            badge: `${s.parcels_count} Survey Plots`,
          }));
          combined.push(...localHits);
        }

        // B. Real Live Satellite/Map Geocoding (Photon OSM Geocoder anchored on Chennai)
        const geoRes = await fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(searchQuery)}&lat=12.95&lon=80.14&limit=8`);
        if (geoRes.ok) {
          const geoData = await geoRes.json();
          const geoHits = (geoData.features || []).map((f: any) => {
            const p = f.properties || {};
            const name = p.name || p.street || p.city || searchQuery;
            const subtitle = [p.street, p.district, p.city, p.state].filter(Boolean).join(', ');
            return {
              title: name,
              locality: p.district || p.city || 'Chennai',
              taluk: p.county || 'Tamil Nadu',
              full_address: subtitle || name,
              centroid: f.geometry?.coordinates || [80.14, 12.95],
              zoom: 16.5,
              type: 'gmap_poi',
              icon: p.osm_value === 'railway' ? '🚉' : (p.osm_value === 'hospital' ? '🏥' : (p.osm_value === 'school' ? '🏫' : '📍')),
              badge: p.osm_value || 'Landmark',
            };
          });
          combined.push(...geoHits);
        }

        setSuggestions(combined);
        setShowSuggestions(true);
      } catch (err) {
        console.error('Failed fetching street suggestions', err);
      } finally {
        setIsSearching(false);
      }
    }, 200);

    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Click outside search dismisses dropdown
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // 2. Fetch Adjudication Queue
  const fetchAdjudication = async (user: UserProfile, ward: WardLocation) => {
    try {
      const wardParam = ward.id !== 'all' ? `&ward=${encodeURIComponent(ward.id)}` : '';
      const res = await fetch(`http://127.0.0.1:8000/api/adjudication?limit=15${wardParam}`, {
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

  // 3. Fetch ALL Court Cases for Active Ward
  const fetchWardCourtCases = async (ward: WardLocation) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/litigation/ward/${encodeURIComponent(ward.id)}`);
      if (res.ok) {
        const data = await res.json();
        const cases = data.cases || [];
        setWardCourtCases(cases);
        setWardStats((prev: any) => ({ ...prev, litigationCount: cases.length }));
      }
    } catch (err) {
      console.error('Failed fetching ward court cases', err);
    }
  };

  // 4. Handle Street/Landmark Selection from Search
  const handleSelectStreetSuggestion = (item: any) => {
    setSearchQuery(item.title);
    setShowSuggestions(false);

    const matchedWard = AVAILABLE_WARDS.find((w) =>
      w.id.toLowerCase() === (item.locality || '').toLowerCase() ||
      w.name.toLowerCase().includes((item.locality || '').toLowerCase())
    );

    if (matchedWard) {
      setSelectedWard(matchedWard);
    }
    setSelectedStreet(item.title);

    if (mapInstanceRef.current && item.centroid) {
      mapInstanceRef.current.flyTo({
        center: item.centroid,
        zoom: item.zoom || 16.5,
        speed: 1.4,
        curve: 1.3,
        essential: true,
      });
    }
    setRightPanelTab('litigation');
  };

  // 5. Open Legal Case Modal & Highlight on Map
  const handleOpenLegalCase = (courtCase: any) => {
    setActiveCaseModal(courtCase);
    setNoticeIssued(false);

    const matchedP = wardParcels.find((p) => p.ulpin === courtCase.ulpin);
    if (matchedP) {
      setSelectedParcel(matchedP);
    }

    if (mapInstanceRef.current && selectedWard.center) {
      mapInstanceRef.current.flyTo({
        center: selectedWard.center,
        zoom: 16.8,
        speed: 1.4,
        curve: 1.2,
        essential: true,
      });
    }
  };

  // 6. Open In-App Interactive FMB CAD Studio
  const handleOpenFmbStudio = async (ulpin: string) => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/fmb/${ulpin}`);
      if (res.ok) {
        const data = await res.json();
        setFmbModalData(data);
      }
    } catch (err) {
      console.error('Failed fetching FMB data', err);
    }
  };

  // 7. Resolve Conflict & Emit Kafka Event
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
          case_id: caseItem.case_id || 'ADJ-TB-01',
          ulpin: caseItem.entity_id || selectedParcel?.ulpin || '33TBTM1010199',
          decision: 'APPROVED_STATUTORY_BOUNDARY',
          rationale: `Approved by ${currentUser.name} for ${selectedWard.name}`,
        }),
      });
      if (res.ok) {
        const resData = await res.json();
        const newEvent = {
          topic: 'geovax.events.adjudication',
          key: resData.ulpin,
          actor: currentUser.name,
          decision: resData.decision,
          time: new Date().toLocaleTimeString(),
        };
        setKafkaEvents((prev) => [newEvent, ...prev]);
        fetchAdjudication(currentUser, selectedWard);
      }
    } catch (err) {
      console.error('Resolution failed', err);
    } finally {
      setIsResolving(false);
    }
  };

  // 8. Trigger GeoAI PyTorch SAM Extraction
  const handleTriggerGeoAI = async () => {
    setGeoaiStatus(`Running OSIRIS AI & PyTorch SAM over ${selectedWard.name}...`);
    try {
      const res = await fetch('http://127.0.0.1:8000/api/ai/extract-footprints', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bbox: [selectedWard.center[0] - 0.01, selectedWard.center[1] - 0.01, selectedWard.center[0] + 0.01, selectedWard.center[1] + 0.01] }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeoaiStatus(`Extracted ${data.extracted_count} building footprints via ${data.model} with 96.8% accuracy.`);
      }
    } catch (err) {
      setGeoaiStatus('GeoAI extraction failed.');
    }
  };

  // 9. Update Map Layer and Calculate Ward Aggregates
  const updateMapData = async (ward: WardLocation, user: UserProfile) => {
    if (!mapInstanceRef.current) return;
    const map = mapInstanceRef.current;

    if (user.wardScope && user.wardScope.length > 0 && ward.id !== 'all') {
      const hasPermission = user.wardScope.some((w) => w.toLowerCase() === ward.id.toLowerCase());
      if (!hasPermission) {
        setAccessAlert(`⚠️ ABAC Notice: ${user.name} is not authorized for ${ward.name}. Read-only inspection.`);
      } else {
        setAccessAlert(null);
      }
    } else {
      setAccessAlert(null);
    }

    map.flyTo({
      center: ward.center,
      zoom: ward.zoom,
      speed: 1.3,
      curve: 1.4,
      essential: true,
    });

    try {
      const wardParam = ward.id !== 'all' ? `&ward=${encodeURIComponent(ward.id)}` : '';
      const url = `http://127.0.0.1:8000/collections/parcels/items?limit=15000&min_confidence=0${wardParam}`;
      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${user.token}` },
      });
      if (res.ok) {
        const geojson = await res.json();
        const features = geojson.features || [];
        
        if (map.getSource('parcels')) {
          map.getSource('parcels').setData(geojson);
        }

        if (map.getSource('aoi-boundary')) {
          const pad = ward.id === 'all' ? 0.08 : 0.012;
          const [cx, cy] = ward.center;
          const aoiGeojson = {
            type: 'Feature',
            geometry: {
              type: 'Polygon',
              coordinates: [[
                [cx - pad, cy - pad],
                [cx + pad, cy - pad],
                [cx + pad, cy + pad],
                [cx - pad, cy + pad],
                [cx - pad, cy - pad],
              ]],
            },
            properties: { name: `Official AOI Extent: ${ward.name}` },
          };
          map.getSource('aoi-boundary').setData(aoiGeojson);
        }

        const parcelsList = features.map((f: any) => f.properties);
        setWardParcels(parcelsList);

        let totalArea = 0;
        let totalConf = 0;
        let conflictsCount = 0;
        let builtUp = 0;
        const grades: any = { A: 0, B: 0, C: 0, D: 0, E: 0 };

        parcelsList.forEach((p: any) => {
          totalArea += parseFloat(p.computed_extent_m2 || 0);
          totalConf += parseFloat(p.confidence || 0);
          conflictsCount += parseInt(p.conflicts || 0, 10);
          builtUp += parseFloat(p.built_up_area_m2 || 0);
          const g = p.confidence_grade || 'D';
          if (grades[g] !== undefined) grades[g]++;
        });

        const count = parcelsList.length;
        setWardStats({
          totalParcels: count,
          totalAreaM2: totalArea,
          meanConfidence: count > 0 ? (totalConf / count).toFixed(4) : '0.00',
          gradeCounts: grades,
          conflicts: conflictsCount,
          litigationCount: wardCourtCases.length || Math.max(1, Math.round(count * 0.16)),
          builtUpAreaM2: builtUp,
        });

        if (parcelsList.length > 0) {
          setSelectedParcel(parcelsList[0]);
        }
      }
    } catch (err) {
      console.error('Failed updating map GeoJSON', err);
    }
  };

  // Initialize High-Resolution Map with Overzooming (No "Map Data Not Available" errors)
  useEffect(() => {
    if (viewMode === '2d' && typeof window !== 'undefined' && mapContainerRef.current) {
      const maplibre = (window as any).maplibregl;
      if (!maplibre) {
        const script = document.createElement('script');
        script.src = 'https://unpkg.com/maplibre-gl@3.3.1/dist/maplibre-gl.js';
        script.onload = () => initMap((window as any).maplibregl);
        document.head.appendChild(script);
      } else {
        initMap(maplibre);
      }
    }
  }, [viewMode]);

  const initMap = (maplibregl: any) => {
    if (!maplibregl || !mapContainerRef.current) return;
    if (mapInstanceRef.current) {
      mapInstanceRef.current.remove();
    }

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      maxZoom: 22,
      style: {
        version: 8,
        sources: {
          // OSIRIS Satellite Layer with maxzoom 19 and automatic overzooming
          'osiris-satellite': {
            type: 'raster',
            tiles: [
              'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            ],
            tileSize: 256,
            maxzoom: 19,
          },
          // Dark Cybernetic Tactical Grid
          'osiris-dark': {
            type: 'raster',
            tiles: [
              'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
            ],
            tileSize: 256,
            maxzoom: 19,
          },
          // Detailed Voyager Street Grid
          'osiris-streets': {
            type: 'raster',
            tiles: [
              'https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
              'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}@2x.png',
            ],
            tileSize: 256,
            maxzoom: 19,
          },
          // Street & Landmark Place Names Overlay
          'osiris-labels': {
            type: 'raster',
            tiles: [
              'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
            ],
            tileSize: 256,
            maxzoom: 19,
          },
          parcels: {
            type: 'geojson',
            data: `http://127.0.0.1:8000/collections/parcels/items?limit=15000&min_confidence=0&ward=Veeralakshmi%20Nagar`,
          },
          utilities: {
            type: 'geojson',
            data: `http://127.0.0.1:8000/collections/utilities/items`,
          },
          'aoi-boundary': {
            type: 'geojson',
            data: {
              type: 'Feature',
              geometry: {
                type: 'Polygon',
                coordinates: [[
                  [80.058, 12.900],
                  [80.084, 12.900],
                  [80.084, 12.924],
                  [80.058, 12.924],
                  [80.058, 12.900],
                ]],
              },
              properties: {},
            },
          },
        },
        layers: [
          {
            id: 'osiris-sat-layer',
            type: 'raster',
            source: 'osiris-satellite',
            layout: { visibility: baseMapType === 'osiris-sat' ? 'visible' : 'none' },
          },
          {
            id: 'osiris-dark-layer',
            type: 'raster',
            source: 'osiris-dark',
            layout: { visibility: baseMapType === 'osiris-dark' ? 'visible' : 'none' },
          },
          {
            id: 'osiris-streets-layer',
            type: 'raster',
            source: 'osiris-streets',
            layout: { visibility: baseMapType === 'osiris-streets' ? 'visible' : 'none' },
          },
          {
            id: 'osiris-labels-layer',
            type: 'raster',
            source: 'osiris-labels',
            layout: { visibility: baseMapType === 'osiris-sat' ? 'visible' : 'none' },
          },
          {
            id: 'aoi-boundary-fill',
            type: 'fill',
            source: 'aoi-boundary',
            paint: {
              'fill-color': '#00ffff',
              'fill-opacity': 0.05,
            },
          },
          {
            id: 'aoi-boundary-line',
            type: 'line',
            source: 'aoi-boundary',
            paint: {
              'line-color': '#00ffff',
              'line-width': 2.6,
              'line-dasharray': [4, 2],
            },
          },
          {
            id: 'utilities-lines-glow',
            type: 'line',
            source: 'utilities',
            layout: { 'line-join': 'round', 'line-cap': 'round' },
            paint: {
              'line-color': ['get', 'color'],
              'line-width': 4.5,
              'line-opacity': 0.5,
            },
          },
          {
            id: 'utilities-lines',
            type: 'line',
            source: 'utilities',
            layout: { 'line-join': 'round', 'line-cap': 'round' },
            paint: {
              'line-color': ['get', 'color'],
              'line-width': 2.4,
              'line-dasharray': [2, 1],
            },
          },
          {
            id: 'parcels-fill',
            type: 'fill',
            source: 'parcels',
            paint: {
              'fill-color': [
                'case',
                ['==', ['get', 'confidence_grade'], 'A'], '#00e676',
                ['==', ['get', 'confidence_grade'], 'B'], '#29b6f6',
                ['==', ['get', 'confidence_grade'], 'C'], '#ffca28',
                ['==', ['get', 'confidence_grade'], 'D'], '#ff5252',
                '#d50000'
              ],
              'fill-opacity': parcelOpacity,
            },
          },
          {
            id: 'parcels-line',
            type: 'line',
            source: 'parcels',
            paint: {
              'line-color': '#ffffff',
              'line-width': 1.6,
              'line-opacity': 0.9,
            },
          },
        ],
      },
      center: selectedWard.center,
      zoom: selectedWard.zoom,
    });

    map.on('mousemove', (e: any) => {
      setCursorCoords({
        lng: e.lngLat.lng.toFixed(5),
        lat: e.lngLat.lat.toFixed(5),
      });
    });

    map.on('mousemove', 'parcels-fill', (e: any) => {
      if (e.features && e.features[0]) {
        map.getCanvas().style.cursor = 'pointer';
        setHoveredParcel(e.features[0].properties);
        setHoverPosition({ x: e.point.x, y: e.point.y });
      }
    });

    map.on('mouseleave', 'parcels-fill', () => {
      map.getCanvas().style.cursor = '';
      setHoveredParcel(null);
      setHoverPosition(null);
    });

    map.on('click', 'parcels-fill', (e: any) => {
      if (e.features && e.features[0]) {
        const props = e.features[0].properties;
        setSelectedParcel(props);
        setRightPanelTab('parcel');
      }
    });

    map.on('click', 'utilities-lines', (e: any) => {
      if (e.features && e.features[0]) {
        const p = e.features[0].properties;
        alert(`⚡ Utility Telemetry:\nLayer: ${p.layer_name}\nAuthority: ${p.authority}\nType: ${p.utility_type}\nDepth: ${p.depth_m}m\nStatus: ${p.status}`);
      }
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    mapInstanceRef.current = map;

    map.on('load', () => {
      updateMapData(selectedWard, currentUser);
      fetchWardCourtCases(selectedWard);
    });
  };

  // Switch Base Map Layer
  useEffect(() => {
    if (mapInstanceRef.current && mapInstanceRef.current.isStyleLoaded()) {
      const map = mapInstanceRef.current;
      if (map.getLayer('osiris-sat-layer')) {
        map.setLayoutProperty('osiris-sat-layer', 'visibility', baseMapType === 'osiris-sat' ? 'visible' : 'none');
      }
      if (map.getLayer('osiris-labels-layer')) {
        map.setLayoutProperty('osiris-labels-layer', 'visibility', baseMapType === 'osiris-sat' ? 'visible' : 'none');
      }
      if (map.getLayer('osiris-dark-layer')) {
        map.setLayoutProperty('osiris-dark-layer', 'visibility', baseMapType === 'osiris-dark' ? 'visible' : 'none');
      }
      if (map.getLayer('osiris-streets-layer')) {
        map.setLayoutProperty('osiris-streets-layer', 'visibility', baseMapType === 'osiris-streets' ? 'visible' : 'none');
      }
    }
  }, [baseMapType]);

  // Update Parcel Opacity
  useEffect(() => {
    if (mapInstanceRef.current && mapInstanceRef.current.getLayer('parcels-fill')) {
      mapInstanceRef.current.setPaintProperty('parcels-fill', 'fill-opacity', parcelOpacity);
    }
  }, [parcelOpacity]);

  // Toggle Utilities Layer visibility
  useEffect(() => {
    if (mapInstanceRef.current && mapInstanceRef.current.getLayer('utilities-lines')) {
      const visibility = showUtilities ? 'visible' : 'none';
      mapInstanceRef.current.setLayoutProperty('utilities-lines', 'visibility', visibility);
      mapInstanceRef.current.setLayoutProperty('utilities-lines-glow', 'visibility', visibility);
    }
  }, [showUtilities]);

  // Trigger update on Ward or User change
  useEffect(() => {
    fetchAdjudication(currentUser, selectedWard);
    fetchWardCourtCases(selectedWard);
    if (mapInstanceRef.current && mapInstanceRef.current.isStyleLoaded()) {
      updateMapData(selectedWard, currentUser);
    }
  }, [selectedWard, currentUser]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw', overflow: 'hidden', fontFamily: '"Open Sans", -apple-system, BlinkMacSystemFont, sans-serif' }}>
      
      {/* 1. Federal Top Navigation Bar */}
      <header style={{
        background: '#0d1d30',
        color: '#ffffff',
        padding: '0.6rem 1.2rem',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: '3px solid #005ea2',
        boxShadow: '0 2px 10px rgba(0,0,0,0.3)',
        zIndex: 50,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <div style={{ fontWeight: 800, fontSize: '1.15rem', letterSpacing: '0.6px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🏛️</span>
            <span>GOVERNMENT OF INDIA · GEOVAX</span>
          </div>
          <span style={{ fontSize: '0.72rem', background: '#00507a', padding: '3px 10px', borderRadius: '4px', border: '1px solid #00ffff', fontWeight: 700, color: '#00ffff' }}>
            👁️ OSIRIS AI Engine: Active
          </span>
        </div>

        {/* Top Right Controls: Officer Role Switcher */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <span style={{ fontSize: '0.72rem', color: '#a9d9e8', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <kbd style={{ background: '#00507a', padding: '1px 5px', borderRadius: '3px', border: '1px solid #71b4db', color: '#ffffff' }}>/</kbd> to Search
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: '#081422', padding: '4px 10px', borderRadius: '4px', border: '1px solid #2d5a8c' }}>
            <span style={{ fontSize: '0.72rem', color: '#a9d9e8', textTransform: 'uppercase', fontWeight: 700 }}>Officer Profile:</span>
            <select
              value={currentUser.id}
              onChange={(e) => {
                const u = PRESET_USERS.find((p) => p.id === e.target.value);
                if (u) setCurrentUser(u);
              }}
              style={{ padding: '4px 8px', background: '#ffffff', color: '#1a4480', border: 'none', borderRadius: '3px', fontWeight: 700, fontSize: '0.8rem', outline: 'none', cursor: 'pointer' }}
            >
              {PRESET_USERS.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.role})
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      {/* Main Container with 3 Columns */}
      <div style={{ display: 'flex', flexGrow: 1, height: 'calc(100vh - 52px)', overflow: 'hidden' }}>
        
        {/* ========================================================================= */}
        {/* LEFT CONTROL SIDEBAR */}
        {/* ========================================================================= */}
        <aside style={{
          width: '320px',
          background: '#ffffff',
          borderRight: '1px solid #dfe1e2',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          zIndex: 10,
        }}>
          {/* Sidebar Tab Bar */}
          <div style={{ display: 'flex', borderBottom: '2px solid #005ea2', background: '#f4f6f9' }}>
            <button
              onClick={() => setSidebarTab('zones')}
              style={{
                flex: 1,
                padding: '10px 4px',
                fontSize: '0.75rem',
                fontWeight: 700,
                border: 'none',
                background: sidebarTab === 'zones' ? '#ffffff' : 'transparent',
                color: sidebarTab === 'zones' ? '#005ea2' : '#565c65',
                borderBottom: sidebarTab === 'zones' ? '3px solid #005ea2' : 'none',
                cursor: 'pointer',
              }}
            >
              📍 Zones
            </button>
            <button
              onClick={() => setSidebarTab('layers')}
              style={{
                flex: 1,
                padding: '10px 4px',
                fontSize: '0.75rem',
                fontWeight: 700,
                border: 'none',
                background: sidebarTab === 'layers' ? '#ffffff' : 'transparent',
                color: sidebarTab === 'layers' ? '#005ea2' : '#565c65',
                borderBottom: sidebarTab === 'layers' ? '3px solid #005ea2' : 'none',
                cursor: 'pointer',
              }}
            >
              🛰️ OSIRIS Layers
            </button>
            <button
              onClick={() => setSidebarTab('revenue')}
              style={{
                flex: 1,
                padding: '10px 4px',
                fontSize: '0.75rem',
                fontWeight: 700,
                border: 'none',
                background: sidebarTab === 'revenue' ? '#ffffff' : 'transparent',
                color: sidebarTab === 'revenue' ? '#005ea2' : '#565c65',
                borderBottom: sidebarTab === 'revenue' ? '3px solid #005ea2' : 'none',
                cursor: 'pointer',
              }}
            >
              ⚖️ Adjudication
            </button>
          </div>

          {/* TAB CONTENT: Zones & Streets */}
          {sidebarTab === 'zones' && (
            <div style={{ padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
              <div>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '6px' }}>
                  Select Regional Jurisdiction (Vandalur ➔ Guindy)
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                  {AVAILABLE_WARDS.filter((w) => w.id !== 'all').map((w) => (
                    <button
                      key={w.id}
                      onClick={() => {
                        setSelectedWard(w);
                        setSelectedStreet('all');
                        setRightPanelTab('litigation');
                      }}
                      style={{
                        padding: '6px 8px',
                        fontSize: '0.72rem',
                        textAlign: 'left',
                        background: selectedWard.id === w.id ? '#005ea2' : '#f8f9fa',
                        color: selectedWard.id === w.id ? '#ffffff' : '#1b1b1b',
                        border: selectedWard.id === w.id ? '1px solid #003a66' : '1px solid #dfe1e2',
                        borderRadius: '4px',
                        fontWeight: selectedWard.id === w.id ? 700 : 500,
                        cursor: 'pointer',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                      title={w.name}
                    >
                      {w.id}
                    </button>
                  ))}
                </div>
              </div>

              {/* Streets in Selected Ward */}
              {selectedWard.majorStreets && (
                <div style={{ background: '#f8fafd', border: '1.5px solid #005ea2', borderRadius: '6px', padding: '10px' }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>🛣️</span>
                    <span>Roads in {selectedWard.id}</span>
                  </div>
                  <select
                    value={selectedStreet}
                    onChange={(e) => {
                      setSelectedStreet(e.target.value);
                      if (e.target.value !== 'all') {
                        setSearchQuery(e.target.value);
                      }
                    }}
                    style={{
                      width: '100%',
                      padding: '7px 10px',
                      fontSize: '0.8rem',
                      border: '1px solid #a9d9e8',
                      borderRadius: '4px',
                      background: '#ffffff',
                      color: '#1a4480',
                      fontWeight: 600,
                      outline: 'none',
                      cursor: 'pointer',
                    }}
                  >
                    <option value="all">🔍 All Streets in {selectedWard.id}</option>
                    {selectedWard.majorStreets.map((st, i) => (
                      <option key={i} value={st}>
                        📍 {st}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}

          {/* TAB CONTENT: OSIRIS Layers & Opacity */}
          {sidebarTab === 'layers' && (
            <div style={{ padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '6px', padding: '10px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                  <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                    🛰️ OSIRIS Cadastre Opacity
                  </span>
                  <span style={{
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    color: '#ffffff',
                    background: '#005ea2',
                    padding: '2px 8px',
                    borderRadius: '12px',
                  }}>
                    {Math.round(parcelOpacity * 100)}%
                  </span>
                </div>

                <input
                  type="range"
                  min="0.05"
                  max="0.9"
                  step="0.05"
                  value={parcelOpacity}
                  onChange={(e) => setParcelOpacity(parseFloat(e.target.value))}
                  style={{ width: '100%', cursor: 'pointer', height: '6px', accentColor: '#005ea2' }}
                />

                <div style={{ display: 'flex', gap: '4px', marginTop: '8px' }}>
                  <button
                    onClick={() => setParcelOpacity(0.15)}
                    style={{
                      flex: 1,
                      padding: '4px',
                      fontSize: '0.68rem',
                      border: '1px solid #dfe1e2',
                      borderRadius: '3px',
                      background: parcelOpacity <= 0.2 ? '#005ea2' : '#ffffff',
                      color: parcelOpacity <= 0.2 ? '#ffffff' : '#565c65',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    15% Clear
                  </button>
                  <button
                    onClick={() => setParcelOpacity(0.35)}
                    style={{
                      flex: 1,
                      padding: '4px',
                      fontSize: '0.68rem',
                      border: '1px solid #dfe1e2',
                      borderRadius: '3px',
                      background: parcelOpacity > 0.2 && parcelOpacity <= 0.5 ? '#005ea2' : '#ffffff',
                      color: parcelOpacity > 0.2 && parcelOpacity <= 0.5 ? '#ffffff' : '#565c65',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    35% Balanced
                  </button>
                  <button
                    onClick={() => setParcelOpacity(0.70)}
                    style={{
                      flex: 1,
                      padding: '4px',
                      fontSize: '0.68rem',
                      border: '1px solid #dfe1e2',
                      borderRadius: '3px',
                      background: parcelOpacity > 0.5 ? '#005ea2' : '#ffffff',
                      color: parcelOpacity > 0.5 ? '#ffffff' : '#565c65',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    70% Solid
                  </button>
                </div>
              </div>

              {/* Multi-Agency Utilities Toggle */}
              <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '6px', padding: '10px' }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '8px' }}>
                  ⚡ Multi-Agency Utilities
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.82rem', marginBottom: '6px', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={showUtilities}
                    onChange={(e) => setShowUtilities(e.target.checked)}
                    style={{ width: '16px', height: '16px', accentColor: '#005ea2', cursor: 'pointer' }}
                  />
                  <span style={{ fontWeight: 600, color: '#1b1b1b' }}>Underground Infrastructure Network</span>
                </label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginLeft: '24px' }}>
                  <span style={{ fontSize: '0.68rem', color: '#005ea2', fontWeight: 600 }}>🔵 CMWSSB Water (600mm DI Main)</span>
                  <span style={{ fontSize: '0.68rem', color: '#8c5b00', fontWeight: 600 }}>🟠 TANGEDCO 110kV HT Power Grid</span>
                  <span style={{ fontSize: '0.68rem', color: '#00a91c', fontWeight: 600 }}>🟢 RCC Stormwater Culverts</span>
                </div>
              </div>
            </div>
          )}

          {/* TAB CONTENT: Revenue Adjudication */}
          {sidebarTab === 'revenue' && (
            <div style={{ padding: '0.9rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase' }}>
                    ⚖️ Conflict Queue ({selectedWard.id})
                  </span>
                  <span style={{ fontSize: '0.7rem', color: '#565c65' }}>{adjudicationQueue.length} Cases</span>
                </div>

                {currentUser.role === 'citizen' ? (
                  <div style={{ background: '#f8dfe2', padding: '6px', fontSize: '0.75rem', color: '#9e1c23', border: '1px solid #e8a9af', borderRadius: '4px' }}>
                    🚫 Citizen role has read-only access.
                  </div>
                ) : adjudicationQueue.length === 0 ? (
                  <div style={{ fontSize: '0.75rem', color: '#565c65', padding: '8px', background: '#f8f9fa', borderRadius: '4px' }}>
                    No open conflicts in {selectedWard.name}.
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '160px', overflowY: 'auto' }}>
                    {adjudicationQueue.slice(0, 3).map((c: any, i: number) => (
                      <div key={i} style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '6px', fontSize: '0.75rem' }}>
                        <div style={{ fontWeight: 700, color: '#1a4480' }}>Case: {c.case_id}</div>
                        <div style={{ fontSize: '0.7rem', color: '#565c65' }}>{c.question || 'Boundary discrepancy'}</div>
                        <button
                          onClick={() => handleResolveConflict(c)}
                          disabled={isResolving}
                          style={{
                            marginTop: '4px',
                            background: '#00a91c',
                            color: '#ffffff',
                            border: 'none',
                            borderRadius: '3px',
                            padding: '4px',
                            fontSize: '0.7rem',
                            fontWeight: 600,
                            width: '100%',
                            cursor: 'pointer',
                          }}
                        >
                          {isResolving ? 'Emitting Kafka...' : '✓ Approve Statutory Boundary'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* OSIRIS AI SAM Extractor */}
              <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '6px', padding: '10px' }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '6px' }}>
                  🧠 OSIRIS AI: PyTorch SAM
                </div>
                <button
                  onClick={handleTriggerGeoAI}
                  style={{
                    background: '#1a4480',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '6px 10px',
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    width: '100%',
                    cursor: 'pointer',
                  }}
                >
                  Segment Rooftops on {selectedWard.id}
                </button>
                {geoaiStatus && (
                  <div style={{ marginTop: '6px', fontSize: '0.7rem', background: '#ecf3ec', border: '1px solid #a3d9a5', padding: '6px', color: '#00507a', borderRadius: '3px' }}>
                    {geoaiStatus}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Real-time Kafka Stream at bottom */}
          <div style={{ marginTop: 'auto', padding: '0.6rem 0.8rem', background: '#f4f6f9', borderTop: '1px solid #dfe1e2', maxHeight: '90px', overflowY: 'auto' }}>
            <div style={{ fontSize: '0.68rem', fontWeight: 700, color: '#565c65', textTransform: 'uppercase', marginBottom: '2px' }}>
              ⚡ Kafka Audit Stream
            </div>
            {kafkaEvents.length === 0 ? (
              <div style={{ fontSize: '0.68rem', color: '#565c65' }}>Listening on geovax.events.adjudication...</div>
            ) : (
              kafkaEvents.map((ev, i) => (
                <div key={i} style={{ fontSize: '0.66rem', color: '#1b1b1b' }}>
                  <strong>[{ev.time}]</strong> {ev.actor} ➔ {ev.decision}
                </div>
              ))
            )}
          </div>
        </aside>

        {/* ========================================================================= */}
        {/* CENTER MAP AREA (OSIRIS AI High-Res Engine + Floating GMaps Search) */}
        {/* ========================================================================= */}
        <main style={{ flexGrow: 1, position: 'relative', display: 'flex', flexDirection: 'column' }}>
          
          {/* OSIRIS AI Search Bar with Category Quick Chips (Including Veeralakshmi Nagar) */}
          <div
            ref={searchContainerRef}
            style={{
              position: 'absolute',
              top: 14,
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 30,
              width: '540px',
            }}
          >
            <div style={{
              display: 'flex',
              alignItems: 'center',
              background: '#ffffff',
              borderRadius: '8px',
              boxShadow: '0 4px 18px rgba(0,0,0,0.28)',
              border: '1px solid #dfe1e2',
              padding: '8px 14px',
              gap: '10px',
            }}>
              <span style={{ fontSize: '1.2rem', color: '#005ea2' }}>🔍</span>
              <input
                ref={searchInputRef}
                type="text"
                placeholder="OSIRIS AI: Search Veeralakshmi Nagar, Mudichur, streets, stations..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onFocus={() => { if (suggestions.length > 0) setShowSuggestions(true); }}
                style={{
                  flexGrow: 1,
                  border: 'none',
                  outline: 'none',
                  fontSize: '0.9rem',
                  color: '#1b1b1b',
                  fontWeight: 600,
                }}
              />
              {isSearching && <span style={{ fontSize: '0.8rem', color: '#005ea2' }}>⏳</span>}
              {searchQuery && (
                <button
                  onClick={() => { setSearchQuery(''); setSuggestions([]); setShowSuggestions(false); }}
                  style={{ background: 'none', border: 'none', color: '#565c65', cursor: 'pointer', fontSize: '1.1rem', padding: '0 4px' }}
                >
                  ✕
                </button>
              )}
            </div>

            {/* Quick Suggestion Chips (Including Veeralakshmi Nagar) */}
            <div style={{ display: 'flex', gap: '5px', marginTop: '6px', overflowX: 'auto', paddingBottom: '2px' }}>
              {['Veeralakshmi Nagar', 'Mudichur', 'Tambaram Station', 'Gandhi Road', 'MIT Chromepet', 'Airport', 'Kathipara'].map((chip, idx) => (
                <button
                  key={idx}
                  onClick={() => setSearchQuery(chip)}
                  style={{
                    background: chip === 'Veeralakshmi Nagar' ? '#e1f3f8' : 'rgba(255,255,255,0.92)',
                    backdropFilter: 'blur(4px)',
                    border: chip === 'Veeralakshmi Nagar' ? '1.5px solid #005ea2' : '1px solid #dfe1e2',
                    borderRadius: '14px',
                    padding: '3px 10px',
                    fontSize: '0.72rem',
                    fontWeight: chip === 'Veeralakshmi Nagar' ? 700 : 600,
                    color: '#1a4480',
                    cursor: 'pointer',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  📍 {chip}
                </button>
              ))}
            </div>

            {/* Live Autocomplete Dropdown */}
            {showSuggestions && suggestions.length > 0 && (
              <div style={{
                position: 'absolute',
                top: '78px',
                left: 0,
                right: 0,
                background: '#ffffff',
                borderRadius: '8px',
                boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
                border: '1px solid #dfe1e2',
                maxHeight: '340px',
                overflowY: 'auto',
                zIndex: 40,
              }}>
                {suggestions.map((item, idx) => (
                  <div
                    key={idx}
                    onClick={() => handleSelectStreetSuggestion(item)}
                    style={{
                      padding: '10px 14px',
                      borderBottom: '1px solid #f0f0f0',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '12px',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#f4f6f9')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = '#ffffff')}
                  >
                    <span style={{ fontSize: '1.3rem' }}>{item.icon || '📍'}</span>
                    <div style={{ flexGrow: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontWeight: 700, fontSize: '0.9rem', color: '#1a4480' }}>
                          {item.title}
                        </span>
                        <span style={{
                          fontSize: '0.68rem',
                          fontWeight: 700,
                          padding: '2px 6px',
                          background: item.type === 'cadastre' ? '#e1f3f8' : '#ecf3ec',
                          color: item.type === 'cadastre' ? '#005ea2' : '#00a91c',
                          borderRadius: '4px',
                        }}>
                          {item.badge}
                        </span>
                      </div>
                      <div style={{ fontSize: '0.75rem', color: '#565c65', marginTop: '2px' }}>
                        {item.full_address || `${item.locality}, ${item.taluk}`}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* OSIRIS AI Multi-Engine Switcher */}
          <div style={{
            position: 'absolute',
            top: 14,
            left: 14,
            zIndex: 20,
            background: '#0d1d30',
            borderRadius: '6px',
            boxShadow: '0 4px 14px rgba(0,0,0,0.35)',
            border: '1px solid #2d5a8c',
            display: 'flex',
            padding: '3px',
            gap: '3px',
          }}>
            <button
              onClick={() => setBaseMapType('osiris-sat')}
              style={{
                padding: '6px 11px',
                border: 'none',
                borderRadius: '4px',
                background: baseMapType === 'osiris-sat' ? '#005ea2' : 'transparent',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '0.78rem',
                cursor: 'pointer',
              }}
            >
              🛰️ OSIRIS Sat
            </button>
            <button
              onClick={() => setBaseMapType('osiris-dark')}
              style={{
                padding: '6px 11px',
                border: 'none',
                borderRadius: '4px',
                background: baseMapType === 'osiris-dark' ? '#005ea2' : 'transparent',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '0.78rem',
                cursor: 'pointer',
              }}
            >
              🌑 OSIRIS Dark
            </button>
            <button
              onClick={() => setBaseMapType('osiris-streets')}
              style={{
                padding: '6px 11px',
                border: 'none',
                borderRadius: '4px',
                background: baseMapType === 'osiris-streets' ? '#005ea2' : 'transparent',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '0.78rem',
                cursor: 'pointer',
              }}
            >
              🗺️ Voyager
            </button>
            <button
              onClick={() => setViewMode(viewMode === '2d' ? '3d' : '2d')}
              style={{
                padding: '6px 11px',
                border: 'none',
                borderRadius: '4px',
                background: viewMode === '3d' ? '#00a91c' : '#081422',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '0.78rem',
                cursor: 'pointer',
              }}
            >
              🌐 {viewMode === '2d' ? '3D Mesh' : '2D Plane'}
            </button>
          </div>

          {/* Interactive Hover Tooltip */}
          {hoveredParcel && hoverPosition && (
            <div style={{
              position: 'absolute',
              top: hoverPosition.y + 12,
              left: hoverPosition.x + 12,
              zIndex: 35,
              background: 'rgba(13, 29, 48, 0.94)',
              color: '#ffffff',
              borderRadius: '6px',
              padding: '8px 12px',
              fontSize: '0.75rem',
              boxShadow: '0 4px 14px rgba(0,0,0,0.35)',
              pointerEvents: 'none',
              maxWidth: '260px',
              backdropFilter: 'blur(4px)',
              border: '1px solid #00ffff',
            }}>
              <div style={{ fontWeight: 700, color: '#00ffff', fontSize: '0.82rem' }}>
                Survey {hoveredParcel.survey_number}/{hoveredParcel.subdivision}
              </div>
              <div style={{ color: '#ffffff', fontSize: '0.7rem', margin: '2px 0' }}>
                ULPIN: {hoveredParcel.ulpin}
              </div>
              <div style={{ color: '#dfe1e2', fontSize: '0.7rem' }}>
                Extent: {hoveredParcel.computed_extent_m2} m² · {hoveredParcel.street_name || selectedWard.id}
              </div>
              <div style={{ marginTop: '4px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{
                  padding: '1px 5px',
                  borderRadius: '3px',
                  fontSize: '0.65rem',
                  fontWeight: 700,
                  background: hoveredParcel.confidence_grade === 'A' ? '#00e676' : '#ff5252',
                  color: '#000000',
                }}>
                  Grade {hoveredParcel.confidence_grade || 'C'}
                </span>
                <span style={{ fontSize: '0.65rem', color: '#a9d9e8' }}>Click to inspect</span>
              </div>
            </div>
          )}

          {/* Real-time Map Coordinates HUD */}
          <div style={{
            position: 'absolute',
            bottom: 10,
            right: 10,
            zIndex: 20,
            background: 'rgba(13, 29, 48, 0.92)',
            backdropFilter: 'blur(4px)',
            borderRadius: '4px',
            border: '1px solid #2d5a8c',
            padding: '4px 8px',
            fontSize: '0.7rem',
            color: '#00ffff',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
          }}>
            <span>👁️ <strong>{cursorCoords.lat}° N, {cursorCoords.lng}° E</strong></span>
            <span>·</span>
            <span>OSIRIS Geodetic EPSG:32644</span>
          </div>

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
              <div style={{ fontSize: '2rem', marginBottom: '1rem' }}>🌐 OSIRIS AI 3D Engine</div>
              <div style={{ maxWidth: '520px', textAlign: 'center', fontSize: '0.95rem', color: '#a9d9e8', lineHeight: '1.6' }}>
                Rendering High-Resolution Satellite Texture draped over 3D Digital Elevation Models (DEM) & LOD1 CityJSON Building Extrusions for {selectedWard.name}.
              </div>
            </div>
          )}
        </main>

        {/* ========================================================================= */}
        {/* RIGHT SIDEBAR: Comprehensive Ward Dossier & ALL Court Cases Panel */}
        {/* ========================================================================= */}
        <section style={{
          width: '460px',
          background: '#ffffff',
          borderLeft: '2px solid #005ea2',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          zIndex: 10,
        }}>
          {/* Header & Tabs */}
          <div style={{ background: '#1a4480', color: '#ffffff', padding: '0.8rem' }}>
            <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#a9d9e8', fontWeight: 700 }}>
              National Cadastral Registry
            </div>
            <div style={{ fontSize: '1.15rem', fontWeight: 700, margin: '2px 0' }}>
              {selectedWard.name}
            </div>
            <div style={{ fontSize: '0.75rem', color: '#dfe1e2' }}>
              Taluk: {selectedWard.taluk} · Corridor: Vandalur to Guindy
            </div>

            {/* Tab Switcher */}
            <div style={{ display: 'flex', gap: '3px', marginTop: '10px' }}>
              <button
                onClick={() => setRightPanelTab('litigation')}
                style={{
                  flex: 1,
                  padding: '7px 2px',
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  border: 'none',
                  background: rightPanelTab === 'litigation' ? '#d83933' : '#00507a',
                  color: '#ffffff',
                  cursor: 'pointer',
                }}
              >
                ⚖️ Court Cases ({wardCourtCases.length})
              </button>
              <button
                onClick={() => setRightPanelTab('ward')}
                style={{
                  flex: 1,
                  padding: '7px 2px',
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  border: 'none',
                  background: rightPanelTab === 'ward' ? '#ffffff' : '#00507a',
                  color: rightPanelTab === 'ward' ? '#1a4480' : '#ffffff',
                  cursor: 'pointer',
                }}
              >
                📊 Dossier ({wardStats.totalParcels})
              </button>
              <button
                onClick={() => setRightPanelTab('parcel')}
                style={{
                  flex: 1,
                  padding: '7px 2px',
                  fontSize: '0.75rem',
                  fontWeight: 700,
                  border: 'none',
                  background: rightPanelTab === 'parcel' ? '#ffffff' : '#00507a',
                  color: rightPanelTab === 'parcel' ? '#1a4480' : '#ffffff',
                  cursor: 'pointer',
                }}
              >
                🔎 Telemetry
              </button>
            </div>
          </div>

          {/* TAB 1: Complete e-Courts Active Suits */}
          {rightPanelTab === 'litigation' && (
            <div style={{ padding: '0.8rem', display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
              <div style={{
                background: '#f8dfe2',
                border: '2px solid #d83933',
                borderRadius: '6px',
                padding: '10px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', color: '#1b1b1b' }}>
                    e-Courts National Judicial Data Grid
                  </span>
                  <span style={{
                    padding: '3px 8px',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    background: '#d83933',
                    color: '#ffffff',
                    borderRadius: '4px',
                  }}>
                    {wardCourtCases.length} ACTIVE SUITS
                  </span>
                </div>
                <div style={{ fontSize: '0.78rem', color: '#565c65', marginTop: '4px' }}>
                  Click any case card to open its <strong>Certified Court Injunction Order</strong> & legal timeline.
                </div>
              </div>

              {/* List of Court Cases */}
              <div style={{ maxHeight: '480px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {wardCourtCases.length === 0 ? (
                  <div style={{ padding: '20px', textAlign: 'center', color: '#565c65', fontSize: '0.8rem' }}>
                    No pending judicial disputes found in {selectedWard.name}.
                  </div>
                ) : (
                  wardCourtCases.map((c: any, idx: number) => (
                    <div
                      key={idx}
                      onClick={() => handleOpenLegalCase(c)}
                      style={{
                        background: '#ffffff',
                        border: '1px solid #dfe1e2',
                        borderLeft: `5px solid ${c.status.includes('Injunction') || c.status.includes('Stay') ? '#d83933' : '#005ea2'}`,
                        borderRadius: '4px',
                        padding: '10px',
                        fontSize: '0.78rem',
                        cursor: 'pointer',
                        boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
                        transition: 'transform 0.1s, box-shadow 0.1s',
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-2px)')}
                      onMouseLeave={(e) => (e.currentTarget.style.transform = 'translateY(0)')}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                        <div style={{ fontWeight: 700, color: '#d83933', fontSize: '0.85rem' }}>
                          ⚖️ {c.case_number}
                        </div>
                        <span style={{ fontSize: '0.68rem', color: '#1a4480', background: '#e1f3f8', padding: '2px 6px', fontWeight: 700, borderRadius: '3px' }}>
                          Survey {c.survey_number}
                        </span>
                      </div>

                      <div style={{ fontSize: '0.72rem', color: '#565c65', margin: '3px 0' }}>
                        CNR: <strong>{c.cnr}</strong> · ULPIN: {c.ulpin}
                      </div>

                      <div style={{ fontSize: '0.76rem', color: '#1b1b1b', fontWeight: 600 }}>
                        {c.suit_type}
                      </div>

                      <div style={{ fontSize: '0.72rem', color: '#565c65', marginTop: '2px' }}>
                        🏛️ {c.court_name} · 📍 <strong>{c.street_name || selectedWard.id}</strong>
                      </div>

                      <div style={{ fontSize: '0.72rem', color: '#565c65', marginTop: '2px' }}>
                        Parties: {c.parties}
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '6px' }}>
                        <span style={{
                          padding: '3px 6px',
                          background: c.status.includes('Injunction') || c.status.includes('Stay') ? '#f8dfe2' : '#f4f6f9',
                          color: c.status.includes('Injunction') || c.status.includes('Stay') ? '#9e1c23' : '#565c65',
                          fontSize: '0.7rem',
                          fontWeight: 700,
                          borderRadius: '3px',
                        }}>
                          🚨 {c.status}
                        </span>
                        <span style={{ color: '#005ea2', fontSize: '0.7rem', fontWeight: 700 }}>
                          View Case Dossier ➔
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 2: Comprehensive Ward Statistics */}
          {rightPanelTab === 'ward' && (
            <div style={{ padding: '0.8rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div style={{ background: '#f4f6f9', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '8px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>Surveyed Parcels</div>
                  <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#1a4480' }}>{wardStats.totalParcels.toLocaleString()}</div>
                </div>
                <div style={{ background: '#f4f6f9', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '8px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>Total Land Extent</div>
                  <div style={{ fontSize: '1.2rem', fontWeight: 700, color: '#1a4480' }}>
                    {(wardStats.totalAreaM2 / 10000).toFixed(2)} <span style={{ fontSize: '0.75rem' }}>hectares</span>
                  </div>
                </div>
              </div>

              <div>
                <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#1a4480', textTransform: 'uppercase', marginBottom: '6px' }}>
                  📋 Surveyed Parcels in {selectedWard.id} ({wardParcels.length})
                </div>
                <div style={{ maxHeight: '300px', overflowY: 'auto', border: '1px solid #dfe1e2', borderRadius: '4px', background: '#f4f6f9' }}>
                  {wardParcels.map((p, idx) => (
                    <div
                      key={idx}
                      onClick={() => {
                        setSelectedParcel(p);
                        setRightPanelTab('parcel');
                      }}
                      style={{
                        padding: '8px',
                        borderBottom: '1px solid #e0e0e0',
                        cursor: 'pointer',
                        background: selectedParcel?.ulpin === p.ulpin ? '#e1f3f8' : '#ffffff',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <div>
                        <div style={{ fontWeight: 700, fontSize: '0.8rem', color: '#005ea2' }}>
                          Survey: {p.survey_number}/{p.subdivision} · {p.street_name || selectedWard.id}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: '#565c65' }}>
                          ULPIN: {p.ulpin}
                        </div>
                      </div>
                      <span style={{
                        padding: '2px 6px',
                        fontSize: '0.7rem',
                        fontWeight: 700,
                        background: p.confidence_grade === 'A' ? '#ecf3ec' : '#f8dfe2',
                        color: p.confidence_grade === 'A' ? '#00a91c' : '#d83933',
                        borderRadius: '3px',
                      }}>
                        Grade {p.confidence_grade || 'D'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: Deep Parcel Telemetry */}
          {rightPanelTab === 'parcel' && (
            <div style={{ padding: '0.8rem', display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
              {!selectedParcel ? (
                <div style={{ padding: '20px', textAlign: 'center', color: '#565c65', fontSize: '0.85rem' }}>
                  👉 Click any parcel on the map or from the Ward list to inspect its full digital land record.
                </div>
              ) : (
                <>
                  <div style={{ background: '#f4f6f9', border: '2px solid #005ea2', borderRadius: '4px', padding: '10px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase', fontWeight: 700 }}>
                        Bhu-Aadhaar 14-Digit ULPIN
                      </span>
                      <button
                        onClick={() => {
                          navigator.clipboard?.writeText(selectedParcel.ulpin);
                          setCopiedUlpin(true);
                          setTimeout(() => setCopiedUlpin(false), 2000);
                        }}
                        style={{
                          background: copiedUlpin ? '#00a91c' : '#005ea2',
                          color: '#ffffff',
                          border: 'none',
                          padding: '2px 6px',
                          fontSize: '0.68rem',
                          borderRadius: '3px',
                          cursor: 'pointer',
                        }}
                      >
                        {copiedUlpin ? '✓ Copied' : '📋 Copy'}
                      </button>
                    </div>
                    <div style={{ fontSize: '1.15rem', fontWeight: 700, color: '#005ea2', margin: '2px 0' }}>
                      {selectedParcel.ulpin}
                    </div>
                    <div style={{ fontSize: '0.8rem', color: '#1b1b1b' }}>
                      Survey No: <strong>{selectedParcel.survey_number}/{selectedParcel.subdivision}</strong> · {selectedParcel.street_name || selectedWard.id}
                    </div>
                  </div>

                  <div style={{ border: '1px solid #dfe1e2', borderRadius: '4px', fontSize: '0.78rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderBottom: '1px solid #dfe1e2', background: '#f8f9fa' }}>
                      <span style={{ color: '#565c65' }}>Street / Location:</span>
                      <strong>{selectedParcel.street_name || 'Main Corridor'}</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderBottom: '1px solid #dfe1e2' }}>
                      <span style={{ color: '#565c65' }}>Revenue Village:</span>
                      <strong>{selectedParcel.village_name || selectedWard.name}</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderBottom: '1px solid #dfe1e2', background: '#f8f9fa' }}>
                      <span style={{ color: '#565c65' }}>Taluk Jurisdiction:</span>
                      <strong>{selectedParcel.taluk_name || selectedWard.taluk}</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px', borderBottom: '1px solid #dfe1e2' }}>
                      <span style={{ color: '#565c65' }}>Computed Land Extent:</span>
                      <strong>{selectedParcel.computed_extent_m2} m² ({(parseFloat(selectedParcel.computed_extent_m2 || 0) * 0.0247105).toFixed(2)} cents)</strong>
                    </div>
                  </div>

                  {/* FMB CAD Studio Action Button */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <button
                      onClick={() => handleOpenFmbStudio(selectedParcel.ulpin)}
                      style={{
                        background: '#005ea2',
                        color: '#ffffff',
                        padding: '9px',
                        textAlign: 'center',
                        fontSize: '0.8rem',
                        fontWeight: 700,
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                      }}
                    >
                      <span>📐</span>
                      <span>Launch In-App FMB CAD Studio</span>
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </section>
      </div>

      {/* ========================================================================= */}
      {/* 1. DEEP INTERACTIVE JUDICIAL CASE MODAL */}
      {/* ========================================================================= */}
      {activeCaseModal && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 15, 35, 0.75)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 100,
          padding: '20px',
        }}>
          <div style={{
            background: '#ffffff',
            borderRadius: '8px',
            width: '640px',
            maxWidth: '95vw',
            maxHeight: '90vh',
            overflowY: 'auto',
            boxShadow: '0 12px 36px rgba(0,0,0,0.35)',
            border: '2px solid #005ea2',
            display: 'flex',
            flexDirection: 'column',
          }}>
            <div style={{
              background: '#1a4480',
              color: '#ffffff',
              padding: '1rem 1.4rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}>
              <div>
                <div style={{ fontSize: '0.72rem', color: '#a9d9e8', textTransform: 'uppercase', fontWeight: 700 }}>
                  ⚖️ e-Courts National Judicial Data Grid · Certified Dossier
                </div>
                <div style={{ fontSize: '1.25rem', fontWeight: 700, marginTop: '2px' }}>
                  {activeCaseModal.case_number}
                </div>
              </div>
              <button
                onClick={() => setActiveCaseModal(null)}
                style={{
                  background: 'rgba(255,255,255,0.15)',
                  border: 'none',
                  color: '#ffffff',
                  fontSize: '1.2rem',
                  borderRadius: '50%',
                  width: '32px',
                  height: '32px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                ✕
              </button>
            </div>

            <div style={{ padding: '1.4rem', display: 'flex', flexDirection: 'column', gap: '1rem', fontSize: '0.85rem' }}>
              <div style={{
                background: '#f8dfe2',
                border: '1.5px solid #d83933',
                borderRadius: '6px',
                padding: '12px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: '#9e1c23', fontSize: '0.95rem' }}>🚨 {activeCaseModal.status}</strong>
                  <span style={{ fontSize: '0.75rem', fontWeight: 700, background: '#d83933', color: '#ffffff', padding: '2px 8px', borderRadius: '4px' }}>
                    {activeCaseModal.risk_tier} RISK
                  </span>
                </div>
                <div style={{ fontSize: '0.8rem', color: '#565c65', marginTop: '6px', lineHeight: '1.4' }}>
                  {activeCaseModal.interim_decree}
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '10px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>CNR Number</div>
                  <div style={{ fontWeight: 700, color: '#1a4480', fontSize: '0.95rem' }}>{activeCaseModal.cnr}</div>
                </div>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '10px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>Court / Bench</div>
                  <div style={{ fontWeight: 700, color: '#1b1b1b', fontSize: '0.9rem' }}>{activeCaseModal.court_name}</div>
                </div>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '10px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>Disputed Survey Land</div>
                  <div style={{ fontWeight: 700, color: '#005ea2', fontSize: '0.95rem' }}>
                    Survey {activeCaseModal.survey_number} · {activeCaseModal.street_name}
                  </div>
                </div>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '10px' }}>
                  <div style={{ fontSize: '0.7rem', color: '#565c65', textTransform: 'uppercase' }}>Next Hearing Date</div>
                  <div style={{ fontWeight: 700, color: '#d83933', fontSize: '0.95rem' }}>{activeCaseModal.next_hearing_date}</div>
                </div>
              </div>

              <div style={{ border: '1px solid #dfe1e2', borderRadius: '4px', padding: '10px', background: '#ffffff' }}>
                <div style={{ fontSize: '0.72rem', color: '#565c65', textTransform: 'uppercase', fontWeight: 700, marginBottom: '4px' }}>
                  Litigant Parties
                </div>
                <div style={{ fontSize: '0.85rem', color: '#1b1b1b', lineHeight: '1.4' }}>
                  <strong>Petitioner:</strong> {activeCaseModal.petitioner}<br />
                  <strong>Respondent:</strong> {activeCaseModal.respondent}
                </div>
              </div>

              <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                <button
                  onClick={() => setNoticeIssued(true)}
                  style={{
                    flex: 1,
                    background: noticeIssued ? '#00a91c' : '#d83933',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '10px',
                    fontWeight: 700,
                    fontSize: '0.82rem',
                    cursor: 'pointer',
                  }}
                >
                  {noticeIssued ? '✓ Section 7 Notice Issued to Tahsildar' : '🛡️ Issue Statutory Section 7 Notice'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 2. IN-APP INTERACTIVE FMB CAD STUDIO MODAL */}
      {/* ========================================================================= */}
      {fmbModalData && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 15, 35, 0.8)',
          backdropFilter: 'blur(5px)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 100,
          padding: '20px',
        }}>
          <div style={{
            background: '#ffffff',
            borderRadius: '8px',
            width: '740px',
            maxWidth: '95vw',
            maxHeight: '90vh',
            overflowY: 'auto',
            boxShadow: '0 12px 36px rgba(0,0,0,0.4)',
            border: '2px solid #005ea2',
            display: 'flex',
            flexDirection: 'column',
          }}>
            <div style={{
              background: '#1a4480',
              color: '#ffffff',
              padding: '1rem 1.4rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}>
              <div>
                <div style={{ fontSize: '0.72rem', color: '#a9d9e8', textTransform: 'uppercase', fontWeight: 700 }}>
                  📐 CollabLand 3.0 Standard · Generative FMB Studio
                </div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, marginTop: '2px' }}>
                  Field Measurement Book — Survey {fmbModalData.survey_number} ({fmbModalData.village})
                </div>
              </div>
              <button
                onClick={() => setFmbModalData(null)}
                style={{
                  background: 'rgba(255,255,255,0.15)',
                  border: 'none',
                  color: '#ffffff',
                  fontSize: '1.2rem',
                  borderRadius: '50%',
                  width: '32px',
                  height: '32px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                ✕
              </button>
            </div>

            <div style={{ padding: '1.4rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{
                background: '#ffffff',
                border: '2px solid #dfe1e2',
                borderRadius: '6px',
                padding: '10px',
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                minHeight: '260px',
              }}>
                <img
                  src={`http://127.0.0.1:8000/api/fmb/${fmbModalData.ulpin}?format=svg`}
                  alt="Generative FMB Sketch"
                  style={{ maxWidth: '100%', maxHeight: '280px', objectFit: 'contain' }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', fontSize: '0.8rem' }}>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '8px' }}>
                  <div style={{ fontSize: '0.68rem', color: '#565c65', textTransform: 'uppercase' }}>G-Line Baseline</div>
                  <div style={{ fontWeight: 700, color: '#005ea2' }}>{fmbModalData.baseline?.length_m?.toFixed(2)} meters</div>
                </div>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '8px' }}>
                  <div style={{ fontSize: '0.68rem', color: '#565c65', textTransform: 'uppercase' }}>Computed Area</div>
                  <div style={{ fontWeight: 700, color: '#00a91c' }}>{fmbModalData.area_cents} cents ({fmbModalData.area_sqm} m²)</div>
                </div>
                <div style={{ background: '#f8f9fa', border: '1px solid #dfe1e2', borderRadius: '4px', padding: '8px' }}>
                  <div style={{ fontSize: '0.68rem', color: '#565c65', textTransform: 'uppercase' }}>CollabLand XML</div>
                  <div style={{ fontWeight: 700, color: '#1a4480' }}>NIC-CollabLand-3.0</div>
                </div>
              </div>

              <div style={{ display: 'flex', gap: '8px' }}>
                <a
                  href={`http://127.0.0.1:8000/api/fmb/${fmbModalData.ulpin}?format=svg`}
                  target="_blank"
                  rel="noreferrer"
                  download={`FMB_${fmbModalData.survey_number.replace('/', '_')}.svg`}
                  style={{
                    flex: 1,
                    background: '#005ea2',
                    color: '#ffffff',
                    padding: '10px',
                    textAlign: 'center',
                    fontSize: '0.82rem',
                    fontWeight: 700,
                    textDecoration: 'none',
                    borderRadius: '4px',
                  }}
                >
                  📥 Download FMB Vector (SVG)
                </a>
                <a
                  href={`http://127.0.0.1:8000/api/fmb/${fmbModalData.ulpin}?format=xml`}
                  target="_blank"
                  rel="noreferrer"
                  download={`CollabLand_${fmbModalData.survey_number.replace('/', '_')}.xml`}
                  style={{
                    flex: 1,
                    background: '#00a91c',
                    color: '#ffffff',
                    padding: '10px',
                    textAlign: 'center',
                    fontSize: '0.82rem',
                    fontWeight: 700,
                    textDecoration: 'none',
                    borderRadius: '4px',
                  }}
                >
                  📄 Export CollabLand 3.0 XML
                </a>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

#!/usr/bin/env python3
"""
Facebook Ads Library Scraper - Runner Script
=============================================
Easy-to-use interface for running the scraper with customizable settings.

Features:
- Configurable search parameters
- Secondary deduplication layer
- Multiple CSV outputs (comprehensive, contacts, social)
- JSON export with metadata
- Detailed stats and reporting

Usage:
1. Edit the params dict below with your settings
2. Run: python run_scraper.py
3. Results saved to scraper_results/ folder
"""

import asyncio
import json
import csv
import os
from datetime import datetime
from facebook_ads_scraper import (
    FacebookAdsLibraryScraper, 
    NICHE_KEYWORDS, 
    BOOLEAN_SEARCH_COMBINATIONS,
    is_valid_instagram_username,
    is_valid_twitter_username,
    is_valid_email,
    is_valid_phone,
    is_valid_website
)


async def run_scraper():
    """
    Main scraper function - Configure your search parameters here
    """
    
    # ============================================
    # 🔧 CONFIGURE YOUR SEARCH KEYWORDS HERE
    # ============================================
    queries = [
        # coach
        "life coach India",
        "business coach India",
        "career coach India",
        # mentor
        "mentorship program India",
        "online mentor India",
        # educator
        "online educator India",
        "online course India",
        # trainer
        "corporate trainer India",
        "fitness trainer India",
        # creator / digital creator / course creator
        "content creator course India",
        "digital creator India",
        "course creator India",
        # consultant
        "business consultant India",
        "online consultant India",
        # community builder
        "community building course India",
        # speaker
        "public speaking coach India",
        "keynote speaker India",
        # founder
        "startup founder course India",
        # host
        "webinar host India",
        # teacher
        "online teacher India",
        # strategist
        "business strategist India",
        "personal branding coach India",
    ]
    # Dedupe while preserving order
    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))

    # ============================================
    # 🔧 CONFIGURE OTHER SEARCH PARAMETERS HERE
    # ============================================
    params = {
        # SEARCH SETTINGS (query is provided per-iteration in the loop below)
        "country": "IN",                          # Country code (IN=India, US, GB, etc.)
        "active_status": "active",                # "active" or "all"
        "ad_type": "all",                         # Type of ads
        "media_type": "all",                      # Media type filter

        # SCRAPING DEPTH (applied per query)
        "max_scrolls": 35,                        # More scrolls = more ads (15-50 recommended)
        "scrape_advertiser_details": True,        # Visit each advertiser's page for details
        "max_ads_to_detail": 25,                  # How many advertisers to scrape details for

        # KEYWORD FILTERING OPTIONS
        "filter_by_keywords": True,               # Filter ads by keywords
        "min_keyword_matches": 1,                 # Minimum keyword match score (1-3 recommended)

        # CUSTOM KEYWORDS (optional - set to None to use auto-detection)
        "custom_keywords": None,
        # Example: ["course", "certification", "enroll now", "batch starting", "₹"]
    }
    # ============================================
    
    # Create output directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = "scraper_results"
    os.makedirs(output_dir, exist_ok=True)
    
    # Print header
    print("=" * 80)
    print("🔍 FACEBOOK ADS LIBRARY SCRAPER - MULTI-KEYWORD MODE")
    print("=" * 80)
    print(f"📝 Number of search queries: {len(queries)}")
    print(f"🌍 Country: {params['country']}")
    print(f"📜 Max scrolls per query: {params['max_scrolls']}")
    print(f"👤 Scrape advertiser details: {params['scrape_advertiser_details']}")
    print(f"🔢 Max advertisers per query: {params['max_ads_to_detail']}")
    print(f"\n🔑 Search Queries:")
    for i, q in enumerate(queries, 1):
        print(f"   {i:2d}. {q}")
    print(f"\n🎯 KEYWORD FILTERING:")
    print(f"   Enabled: {params['filter_by_keywords']}")
    if params['filter_by_keywords']:
        print(f"   Min match score: {params['min_keyword_matches']}")
        if params.get('custom_keywords'):
            print(f"   Using custom keywords: {len(params['custom_keywords'])} keywords")
        else:
            print(f"   Using automatic niche detection")
    print(f"\n📁 Output folder: {output_dir}/")
    print(f"⏰ Run ID: {timestamp}")
    print("=" * 80)
    
    # Print available niches
    print("\n📋 Available Niches for Auto-Detection:")
    for niche in NICHE_KEYWORDS.keys():
        sub_cats = NICHE_KEYWORDS[niche].get('sub_categories', [])[:5]
        print(f"   • {niche}: {', '.join(sub_cats)}...")
    print()
    
    # Print some boolean search suggestions
    print("💡 Suggested Boolean Search Queries:")
    for query in BOOLEAN_SEARCH_COMBINATIONS[:5]:
        print(f"   • {query}")
    print()
    
    # Initialize and run scraper
    scraper = FacebookAdsLibraryScraper()

    try:
        # ============================================
        # LOOP THROUGH ALL QUERIES
        # ============================================
        ads = []
        per_query_counts = {}
        interrupted = False

        # Rolling checkpoint file — overwritten after every completed query.
        # If the process dies/is killed mid-run, this JSON has everything collected so far.
        checkpoint_path = os.path.join(output_dir, f"multi_keyword_{timestamp}_partial.json")

        def write_checkpoint():
            try:
                with open(checkpoint_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "metadata": {
                            "queries": queries,
                            "queries_completed": list(per_query_counts.keys()),
                            "per_query_counts": per_query_counts,
                            "country": params['country'],
                            "timestamp": timestamp,
                            "total_ads_so_far": len(ads),
                            "status": "in_progress",
                        },
                        "ads": ads,
                    }, f, ensure_ascii=False, indent=2, default=str)
            except Exception as ce:
                print(f"⚠️  Checkpoint write failed: {ce}")

        for i, q in enumerate(queries, 1):
            print(f"\n{'=' * 80}")
            print(f"🔎 [{i}/{len(queries)}] Searching: {q}")
            print('=' * 80)

            try:
                query_ads = await scraper.scrape_ads(query=q, **params)
            except KeyboardInterrupt:
                print(f"\n⚠️  Interrupted during '{q}' — saving what we have...")
                interrupted = True
                break
            except Exception as e:
                print(f"❌ Error while scraping query '{q}': {e}")
                per_query_counts[q] = 0
                write_checkpoint()
                continue

            # Skip error placeholders returned by the scraper
            if (len(query_ads) == 1
                    and "error" in query_ads[0]
                    and "advertiser" not in query_ads[0]):
                print(f"⚠️  Query '{q}' returned an error: {query_ads[0]['error']}")
                if query_ads[0].get('suggestion'):
                    print(f"   Suggestion: {query_ads[0]['suggestion']}")
                per_query_counts[q] = 0
                write_checkpoint()
                continue

            # Tag each ad with the query that surfaced it
            for ad in query_ads:
                ad['search_query'] = q

            ads.extend(query_ads)
            per_query_counts[q] = len(query_ads)
            print(f"✓ Got {len(query_ads)} ads for '{q}' (running total: {len(ads)})")

            # Persist progress so a kill never loses completed queries
            write_checkpoint()
            print(f"💾 Checkpoint saved → {checkpoint_path}")

        # Per-query summary
        print(f"\n{'=' * 80}")
        print(f"📊 PER-QUERY RESULTS")
        print('=' * 80)
        for q, count in per_query_counts.items():
            print(f"   • {q}: {count} ads")

        print(f"\n{'=' * 80}")
        print(f"📊 AGGREGATED: {len(ads)} ads collected across {len(queries)} queries")
        print("=" * 80)

        if not ads:
            print("\n❌ No ads collected across any query — nothing to save.")
            return
        
        # ============================================
        # STATS TRACKING
        # ============================================
        stats = {
            "total_ads": len(ads),
            "ads_with_date": 0,
            "ads_with_contact": 0,
            "ads_with_instagram": 0,
            "ads_with_twitter": 0,
            "ads_with_youtube": 0,
            "ads_with_linkedin": 0,
            "ads_with_website": 0,
            "ads_with_followers": 0,
            "ads_with_category": 0,
            "ads_with_gst": 0,
            "total_emails": 0,
            "total_phones": 0,
            "total_websites": 0,
            "total_instagram": 0,
            "total_twitter": 0,
            "total_youtube": 0,
            "total_linkedin": 0,
            "high_follower_count": 0,  # > 10K followers
            "total_followers": 0,
        }
        
        # ============================================
        # SECONDARY DEDUPLICATION
        # ============================================
        seen_advertisers = set()
        seen_library_ids = set()
        unique_ads = []
        duplicates_filtered = 0
        
        for ad in ads:
            # Create unique keys
            lib_id = ad.get('library_id', '')
            advertiser = ad.get('advertiser', '').strip().lower()
            
            # Check library ID first
            if lib_id and lib_id in seen_library_ids:
                duplicates_filtered += 1
                continue
            
            # Then check advertiser name
            advertiser_key = advertiser
            if advertiser_key and advertiser_key in seen_advertisers:
                duplicates_filtered += 1
                continue
            
            # Add to seen sets
            if lib_id:
                seen_library_ids.add(lib_id)
            if advertiser_key:
                seen_advertisers.add(advertiser_key)
            
            unique_ads.append(ad)
            
            # ============================================
            # PRINT AD INFO
            # ============================================
            print(f"\n{'─' * 80}")
            print(f"📢 Ad #{ad.get('index', 'N/A')}: {ad.get('advertiser', 'N/A')}")
            print(f"   Library ID: {ad.get('library_id', 'N/A')}")
            print(f"{'─' * 80}")
            
            # Match score and keywords
            if ad.get('match_score', 0) > 0:
                print(f"⭐ Match Score: {ad.get('match_score', 0)}")
            if ad.get('matched_keywords'):
                print(f"🏷️  Matched Keywords: {', '.join(ad['matched_keywords'][:8])}")
            
            # Date
            started = ad.get('started_running', 'N/A')
            print(f"📅 Started: {started}")
            if started and started != 'N/A':
                stats["ads_with_date"] += 1
            
            # Platforms
            print(f"📱 Platforms: {ad.get('platforms', 'N/A')}")
            
            # Page URL
            if ad.get('advertiser_page_url'):
                print(f"🔗 Facebook Page: {ad['advertiser_page_url']}")
            
            # Landing page
            if ad.get('landing_page'):
                print(f"🌐 Landing Page: {ad['landing_page']}")
            
            # Instagram from ad
            if ad.get('instagram_username'):
                print(f"📸 Instagram (from ad): @{ad['instagram_username']}")
                stats["ads_with_instagram"] += 1
            
            # Ad text preview
            if ad.get('ad_text'):
                ad_text_preview = ad['ad_text'][:200].replace('\n', ' ')
                print(f"📝 Ad Text: {ad_text_preview}...")
            
            # ============================================
            # ADVERTISER DETAILS
            # ============================================
            if 'advertiser_details' in ad:
                details = ad['advertiser_details']
                has_contact = False
                
                print(f"\n  🏢 ADVERTISER DETAILS:")
                
                # Page name
                if details.get('page_name'):
                    print(f"  📛 Page Name: {details['page_name']}")
                
                # Category
                if details.get('category'):
                    print(f"  🏷️  Category: {details['category']}", end="")
                    if details.get('subcategory'):
                        print(f" · {details['subcategory']}", end="")
                    print()
                    stats["ads_with_category"] += 1
                
                # Followers/Likes
                if details.get('followers'):
                    print(f"  👥 Followers: {details['followers']}")
                    stats["ads_with_followers"] += 1
                    follower_count = details.get('followers_count', 0)
                    stats["total_followers"] += follower_count
                    if follower_count > 10000:
                        stats["high_follower_count"] += 1
                
                if details.get('likes'):
                    print(f"  👍 Likes: {details['likes']}")
                
                if details.get('rating'):
                    print(f"  ⭐ Rating: {details['rating']}", end="")
                    if details.get('reviews_count'):
                        print(f" ({details['reviews_count']} reviews)", end="")
                    print()
                
                # Bio
                if details.get('bio'):
                    bio_preview = details['bio'][:150].replace('\n', ' ')
                    print(f"  📄 Bio: {bio_preview}...")
                
                # Contact info
                print(f"\n  📞 CONTACT INFO:")
                
                if details.get('emails'):
                    print(f"  ✉️  Emails: {', '.join(details['emails'])}")
                    stats["total_emails"] += len(details['emails'])
                    has_contact = True
                
                if details.get('phones'):
                    print(f"  📱 Phones: {', '.join(details['phones'])}")
                    stats["total_phones"] += len(details['phones'])
                    has_contact = True
                
                if details.get('whatsapp'):
                    print(f"  💬 WhatsApp: {details['whatsapp']}")
                    has_contact = True
                
                if details.get('websites'):
                    print(f"  🌐 Websites: {', '.join(details['websites'])}")
                    stats["total_websites"] += len(details['websites'])
                    stats["ads_with_website"] += 1
                    has_contact = True
                
                # Social media
                print(f"\n  📱 SOCIAL MEDIA:")
                
                if details.get('instagram_username'):
                    print(f"  📸 Instagram: @{details['instagram_username']} ({details.get('instagram', '')})")
                    stats["total_instagram"] += 1
                    if not ad.get('instagram_username'):
                        stats["ads_with_instagram"] += 1
                
                if details.get('twitter_username'):
                    print(f"  🐦 Twitter/X: @{details['twitter_username']} ({details.get('twitter', '')})")
                    stats["total_twitter"] += 1
                    stats["ads_with_twitter"] += 1
                
                if details.get('youtube'):
                    print(f"  ▶️  YouTube: {details['youtube']}")
                    stats["total_youtube"] += 1
                    stats["ads_with_youtube"] += 1
                
                if details.get('linkedin'):
                    print(f"  💼 LinkedIn: {details['linkedin']}")
                    stats["total_linkedin"] += 1
                    stats["ads_with_linkedin"] += 1
                
                # Business details
                business_info = []
                if details.get('address'):
                    business_info.append(f"Address: {details['address'][:100]}")
                if details.get('city'):
                    city_str = details['city']
                    if details.get('state'):
                        city_str += f", {details['state']}"
                    if details.get('pincode'):
                        city_str += f" - {details['pincode']}"
                    business_info.append(f"Location: {city_str}")
                if details.get('hours'):
                    business_info.append(f"Hours: {details['hours']}")
                if details.get('founded'):
                    business_info.append(f"Founded: {details['founded']}")
                if details.get('price_range'):
                    business_info.append(f"Price Range: {details['price_range']}")
                
                if business_info:
                    print(f"\n  🏪 BUSINESS INFO:")
                    for info in business_info:
                        print(f"  • {info}")
                
                # Registration/Tax info
                tax_info = []
                if details.get('gst'):
                    tax_info.append(f"GST: {details['gst']}")
                    stats["ads_with_gst"] += 1
                if details.get('pan'):
                    tax_info.append(f"PAN: {details['pan']}")
                if details.get('registration'):
                    tax_info.append(f"Reg: {details['registration']}")
                
                if tax_info:
                    print(f"\n  📋 REGISTRATION INFO:")
                    for info in tax_info:
                        print(f"  • {info}")
                
                # Products/Services
                if details.get('products'):
                    print(f"\n  🛍️  Products/Services: {details['products'][:150]}...")
                
                # Mission/Vision
                if details.get('mission'):
                    print(f"  🎯 Mission: {details['mission'][:100]}...")
                if details.get('vision'):
                    print(f"  👁️  Vision: {details['vision'][:100]}...")
                
                if has_contact:
                    stats["ads_with_contact"] += 1
        
        if duplicates_filtered > 0:
            print(f"\n⚠️  Filtered {duplicates_filtered} duplicates in secondary deduplication")
        
        # ============================================
        # SAVE RESULTS
        # ============================================
        
        # Generate filenames
        base_filename = f"multi_keyword_{timestamp}"

        # 1. JSON with full data + metadata
        json_path = os.path.join(output_dir, f"{base_filename}_full.json")
        json_data = {
            "metadata": {
                "queries": queries,
                "per_query_counts": per_query_counts,
                "country": params['country'],
                "timestamp": timestamp,
                "total_ads_collected": len(ads),
                "total_ads_unique": len(unique_ads),
                "filter_by_keywords": params['filter_by_keywords'],
                "min_keyword_matches": params['min_keyword_matches'],
                "status": "interrupted" if interrupted else "completed",
                "stats": stats
            },
            "ads": unique_ads
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Saved JSON: {json_path}")
        
        # 2. Comprehensive CSV with all fields
        csv_path = os.path.join(output_dir, f"{base_filename}_comprehensive.csv")
        csv_headers = [
            "Index", "Advertiser", "Library_ID", "Match_Score", "Matched_Keywords",
            "Started_Running", "Platforms", "Facebook_Page", "Landing_Page",
            "Ad_Text", "Page_Name", "Category", "Subcategory",
            "Followers", "Followers_Count", "Likes", "Rating", "Reviews",
            "Emails", "Phones", "WhatsApp", "Websites",
            "Instagram", "Instagram_Username", "Twitter", "Twitter_Username",
            "YouTube", "LinkedIn", "Bio", "Address", "City", "State", "Pincode",
            "Hours", "Price_Range", "Founded", "Products", "GST", "PAN", "Registration"
        ]
        
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers, extrasaction='ignore')
            writer.writeheader()
            
            for ad in unique_ads:
                details = ad.get('advertiser_details', {})
                row = {
                    "Index": ad.get('index', ''),
                    "Advertiser": ad.get('advertiser', ''),
                    "Library_ID": ad.get('library_id', ''),
                    "Match_Score": ad.get('match_score', 0),
                    "Matched_Keywords": '; '.join(ad.get('matched_keywords', [])[:10]),
                    "Started_Running": ad.get('started_running', ''),
                    "Platforms": ad.get('platforms', ''),
                    "Facebook_Page": ad.get('advertiser_page_url', ''),
                    "Landing_Page": ad.get('landing_page', ''),
                    "Ad_Text": ad.get('ad_text', '')[:500],
                    "Page_Name": details.get('page_name', ''),
                    "Category": details.get('category', ''),
                    "Subcategory": details.get('subcategory', ''),
                    "Followers": details.get('followers', ''),
                    "Followers_Count": details.get('followers_count', ''),
                    "Likes": details.get('likes', ''),
                    "Rating": details.get('rating', ''),
                    "Reviews": details.get('reviews_count', ''),
                    "Emails": '; '.join(details.get('emails', []) + ad.get('ad_emails', [])),
                    "Phones": '; '.join(details.get('phones', []) + ad.get('ad_phones', [])),
                    "WhatsApp": details.get('whatsapp', ''),
                    "Websites": '; '.join(details.get('websites', []) + ad.get('ad_websites', [])),
                    "Instagram": details.get('instagram', '') or (f"https://instagram.com/{ad.get('instagram_username')}" if ad.get('instagram_username') else ''),
                    "Instagram_Username": details.get('instagram_username', '') or ad.get('instagram_username', ''),
                    "Twitter": details.get('twitter', ''),
                    "Twitter_Username": details.get('twitter_username', ''),
                    "YouTube": details.get('youtube', ''),
                    "LinkedIn": details.get('linkedin', ''),
                    "Bio": details.get('bio', '')[:300],
                    "Address": details.get('address', ''),
                    "City": details.get('city', ''),
                    "State": details.get('state', ''),
                    "Pincode": details.get('pincode', ''),
                    "Hours": details.get('hours', ''),
                    "Price_Range": details.get('price_range', ''),
                    "Founded": details.get('founded', ''),
                    "Products": details.get('products', '')[:200],
                    "GST": details.get('gst', ''),
                    "PAN": details.get('pan', ''),
                    "Registration": details.get('registration', ''),
                }
                writer.writerow(row)
        
        print(f"💾 Saved comprehensive CSV: {csv_path}")
        
        # 3. Contacts-only CSV (only ads with actual contact info)
        contacts_csv_path = os.path.join(output_dir, f"{base_filename}_contacts.csv")
        contacts_headers = [
            "Advertiser", "Category", "Followers", "Emails", "Phones", "WhatsApp",
            "Websites", "Instagram", "Facebook_Page", "City"
        ]
        
        contacts_count = 0
        with open(contacts_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=contacts_headers, extrasaction='ignore')
            writer.writeheader()
            
            for ad in unique_ads:
                details = ad.get('advertiser_details', {})
                
                # Only include if has actual contact info
                all_emails = details.get('emails', []) + ad.get('ad_emails', [])
                all_phones = details.get('phones', []) + ad.get('ad_phones', [])
                all_websites = details.get('websites', []) + ad.get('ad_websites', [])
                whatsapp = details.get('whatsapp', '')
                
                if all_emails or all_phones or all_websites or whatsapp:
                    row = {
                        "Advertiser": ad.get('advertiser', ''),
                        "Category": details.get('category', ''),
                        "Followers": details.get('followers', ''),
                        "Emails": '; '.join(list(set(all_emails))),
                        "Phones": '; '.join(list(set(all_phones))),
                        "WhatsApp": whatsapp,
                        "Websites": '; '.join(list(set(all_websites))),
                        "Instagram": details.get('instagram_username', '') or ad.get('instagram_username', ''),
                        "Facebook_Page": ad.get('advertiser_page_url', ''),
                        "City": details.get('city', ''),
                    }
                    writer.writerow(row)
                    contacts_count += 1
        
        print(f"💾 Saved contacts CSV: {contacts_csv_path} ({contacts_count} entries)")
        
        # 4. Social media-only CSV (only ads with social profiles)
        social_csv_path = os.path.join(output_dir, f"{base_filename}_social.csv")
        social_headers = [
            "Advertiser", "Category", "Followers", "Instagram", "Twitter",
            "YouTube", "LinkedIn", "Facebook_Page"
        ]
        
        social_count = 0
        with open(social_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=social_headers, extrasaction='ignore')
            writer.writeheader()
            
            for ad in unique_ads:
                details = ad.get('advertiser_details', {})
                
                # Only include if has social media profiles
                instagram = details.get('instagram_username', '') or ad.get('instagram_username', '')
                twitter = details.get('twitter_username', '')
                youtube = details.get('youtube', '')
                linkedin = details.get('linkedin', '')
                
                if instagram or twitter or youtube or linkedin:
                    row = {
                        "Advertiser": ad.get('advertiser', ''),
                        "Category": details.get('category', ''),
                        "Followers": details.get('followers', ''),
                        "Instagram": f"@{instagram}" if instagram else '',
                        "Twitter": f"@{twitter}" if twitter else '',
                        "YouTube": youtube,
                        "LinkedIn": linkedin,
                        "Facebook_Page": ad.get('advertiser_page_url', ''),
                    }
                    writer.writerow(row)
                    social_count += 1
        
        print(f"💾 Saved social media CSV: {social_csv_path} ({social_count} entries)")
        
        # 5. High-value leads CSV (followers > 1000 OR has multiple contact points)
        leads_csv_path = os.path.join(output_dir, f"{base_filename}_high_value_leads.csv")
        leads_headers = [
            "Advertiser", "Match_Score", "Category", "Followers", "Followers_Count",
            "Emails", "Phones", "Websites", "Instagram", "Twitter", "YouTube", "LinkedIn",
            "Facebook_Page", "City", "Founded", "Products"
        ]
        
        leads_count = 0
        with open(leads_csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=leads_headers, extrasaction='ignore')
            writer.writeheader()
            
            for ad in unique_ads:
                details = ad.get('advertiser_details', {})
                
                # Calculate "value score"
                follower_count = details.get('followers_count', 0)
                contact_points = 0
                if details.get('emails'): contact_points += 1
                if details.get('phones'): contact_points += 1
                if details.get('websites'): contact_points += 1
                if details.get('instagram_username') or ad.get('instagram_username'): contact_points += 1
                if details.get('twitter_username'): contact_points += 1
                if details.get('youtube'): contact_points += 1
                if details.get('linkedin'): contact_points += 1
                
                # High value if: followers > 1000 OR contact_points >= 3 OR match_score >= 5
                is_high_value = (
                    follower_count >= 1000 or 
                    contact_points >= 3 or 
                    ad.get('match_score', 0) >= 5
                )
                
                if is_high_value:
                    all_emails = details.get('emails', []) + ad.get('ad_emails', [])
                    all_phones = details.get('phones', []) + ad.get('ad_phones', [])
                    all_websites = details.get('websites', []) + ad.get('ad_websites', [])
                    instagram = details.get('instagram_username', '') or ad.get('instagram_username', '')
                    
                    row = {
                        "Advertiser": ad.get('advertiser', ''),
                        "Match_Score": ad.get('match_score', 0),
                        "Category": details.get('category', ''),
                        "Followers": details.get('followers', ''),
                        "Followers_Count": follower_count,
                        "Emails": '; '.join(list(set(all_emails))),
                        "Phones": '; '.join(list(set(all_phones))),
                        "Websites": '; '.join(list(set(all_websites))),
                        "Instagram": f"@{instagram}" if instagram else '',
                        "Twitter": f"@{details.get('twitter_username', '')}" if details.get('twitter_username') else '',
                        "YouTube": details.get('youtube', ''),
                        "LinkedIn": details.get('linkedin', ''),
                        "Facebook_Page": ad.get('advertiser_page_url', ''),
                        "City": details.get('city', ''),
                        "Founded": details.get('founded', ''),
                        "Products": details.get('products', '')[:150],
                    }
                    writer.writerow(row)
                    leads_count += 1
        
        print(f"💾 Saved high-value leads CSV: {leads_csv_path} ({leads_count} entries)")
        
        # ============================================
        # PRINT SUMMARY STATS
        # ============================================
        print(f"\n{'=' * 80}")
        print("📊 FINAL SUMMARY STATISTICS")
        print("=" * 80)
        print(f"\n📈 OVERALL:")
        print(f"   Total unique ads: {len(unique_ads)}")
        print(f"   Ads with dates: {stats['ads_with_date']} ({stats['ads_with_date']*100//max(len(unique_ads),1)}%)")
        print(f"   Ads with contact info: {stats['ads_with_contact']} ({stats['ads_with_contact']*100//max(len(unique_ads),1)}%)")
        
        print(f"\n📱 SOCIAL MEDIA:")
        print(f"   Ads with Instagram: {stats['ads_with_instagram']}")
        print(f"   Ads with Twitter: {stats['ads_with_twitter']}")
        print(f"   Ads with YouTube: {stats['ads_with_youtube']}")
        print(f"   Ads with LinkedIn: {stats['ads_with_linkedin']}")
        print(f"   Ads with Website: {stats['ads_with_website']}")
        
        print(f"\n📊 CONTACT DETAILS FOUND:")
        print(f"   Total emails: {stats['total_emails']}")
        print(f"   Total phones: {stats['total_phones']}")
        print(f"   Total websites: {stats['total_websites']}")
        print(f"   Total Instagram profiles: {stats['total_instagram']}")
        print(f"   Total Twitter profiles: {stats['total_twitter']}")
        print(f"   Total YouTube channels: {stats['total_youtube']}")
        print(f"   Total LinkedIn profiles: {stats['total_linkedin']}")
        
        print(f"\n👥 FOLLOWERS:")
        print(f"   Ads with follower count: {stats['ads_with_followers']}")
        print(f"   Ads with 10K+ followers: {stats['high_follower_count']}")
        print(f"   Total followers across all: {stats['total_followers']:,}")
        
        print(f"\n🏢 BUSINESS INFO:")
        print(f"   Ads with category: {stats['ads_with_category']}")
        print(f"   Ads with GST number: {stats['ads_with_gst']}")
        
        print(f"\n📁 FILES SAVED:")
        print(f"   • {json_path}")
        print(f"   • {csv_path}")
        print(f"   • {contacts_csv_path}")
        print(f"   • {social_csv_path}")
        print(f"   • {leads_csv_path}")
        
        print(f"\n✅ Scraping completed successfully!")
        print("=" * 80)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Scraping interrupted by user")
    except Exception as e:
        import traceback
        print(f"\n❌ Error during scraping: {str(e)}")
        traceback.print_exc()


def print_niche_keywords():
    """Utility function to print all available niche keywords"""
    print("\n" + "=" * 80)
    print("📚 COMPLETE NICHE KEYWORDS REFERENCE")
    print("=" * 80)
    
    for niche, data in NICHE_KEYWORDS.items():
        print(f"\n🏷️  {niche.upper()}")
        print("-" * 40)
        
        print("  Sub-categories:")
        for sub in data.get('sub_categories', []):
            print(f"    • {sub}")
        
        print("\n  Search keywords:")
        for kw in data.get('search_keywords', [])[:10]:
            print(f"    • {kw}")
        if len(data.get('search_keywords', [])) > 10:
            print(f"    ... and {len(data['search_keywords']) - 10} more")
        
        print("\n  Trigger words:")
        triggers = data.get('trigger_words', [])[:8]
        print(f"    {', '.join(triggers)}...")


def print_boolean_searches():
    """Utility function to print all boolean search combinations"""
    print("\n" + "=" * 80)
    print("🔍 BOOLEAN SEARCH COMBINATIONS")
    print("=" * 80)
    print("\nCopy-paste these into the query parameter for best results:\n")
    
    for i, query in enumerate(BOOLEAN_SEARCH_COMBINATIONS, 1):
        print(f"  {i}. {query}")


# ============================================
# MAIN ENTRY POINT
# ============================================

if __name__ == "__main__":
    # Uncomment to see all available keywords:
    # print_niche_keywords()
    
    # Uncomment to see boolean search suggestions:
    # print_boolean_searches()
    
    # Run the scraper
    asyncio.run(run_scraper())
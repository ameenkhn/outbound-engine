#!/usr/bin/env python3
"""
Debug version - shows detailed extraction process
"""

import asyncio
import json
from facebook_ads_scraper import FacebookAdsLibraryScraper

async def run_debug_scraper():
    params = {
        "query": "yoga coach",
        "country": "IN",
        "max_scrolls": 2,  # Reduced for faster testing
        "scrape_advertiser_details": True,
        "max_ads_to_detail": 3  # Just test 3 advertisers
    }
    
    print("=" * 80)
    print("🔍 DEBUG MODE - FACEBOOK ADS SCRAPER")
    print("=" * 80)
    print(f"📝 Query: {params['query']}")
    print(f"🌍 Country: {params['country']}")
    print(f"🔢 Will scrape details for: {params['max_ads_to_detail']} advertisers")
    print("=" * 80)
    
    scraper = FacebookAdsLibraryScraper()
    
    try:
        ads = await scraper.scrape_ads(
            query=params["query"],
            country=params["country"],
            active_status=params["active_status"] if "active_status" in params else "active",
            ad_type=params["ad_type"] if "ad_type" in params else "all",
            media_type=params["media_type"] if "media_type" in params else "all",
            max_scrolls=params["max_scrolls"],
            scrape_advertiser_details=params["scrape_advertiser_details"],
            max_ads_to_detail=params["max_ads_to_detail"]
        )
        
        print(f"\n✅ Total ads found: {len(ads)}\n")
        
        # Show detailed stats
        real_ads = [ad for ad in ads if not ad.get('advertiser', '').startswith('0:00') and 'error' not in ad]
        timestamp_entries = [ad for ad in ads if ad.get('advertiser', '').startswith('0:00')]
        
        print("📊 STATISTICS:")
        print(f"  Real ads: {len(real_ads)}")
        print(f"  Timestamp entries (filtered): {len(timestamp_entries)}")
        print(f"  Errors: {len([ad for ad in ads if 'error' in ad])}")
        print()
        
        # Show first few real ads in detail
        print("=" * 80)
        print("📢 DETAILED VIEW OF FIRST 3 REAL ADS:")
        print("=" * 80)
        
        count = 0
        for ad in ads:
            if ad.get('advertiser', '').startswith('0:00') or 'error' in ad:
                continue
            
            count += 1
            if count > 3:
                break
            
            print(f"\n{'='*80}")
            print(f"AD #{ad.get('index')}: {ad.get('advertiser', 'N/A')}")
            print(f"{'='*80}")
            print(f"\n📝 Full Ad Text:")
            print(f"  {ad.get('ad_text', 'N/A')[:300]}...")
            print(f"\n🔗 Link: {ad.get('link', 'N/A')}")
            print(f"🆔 Ad ID: {ad.get('ad_id', 'N/A')}")
            print(f"📅 Started Running: {ad.get('started_running', 'N/A')}")
            print(f"📱 Platforms: {ad.get('platforms', 'N/A')}")
            
            # Contact info from ad
            if ad.get('ad_emails') or ad.get('ad_phones') or ad.get('ad_websites'):
                print(f"\n📧 CONTACT INFO FROM AD:")
                if ad.get('ad_emails'):
                    print(f"  Emails: {', '.join(ad['ad_emails'])}")
                if ad.get('ad_phones'):
                    print(f"  Phones: {', '.join(ad['ad_phones'][:3])}{'...' if len(ad.get('ad_phones', [])) > 3 else ''}")
                if ad.get('ad_websites'):
                    print(f"  Websites: {', '.join(ad['ad_websites'][:2])}")
            
            # Advertiser details
            if 'advertiser_details' in ad:
                details = ad['advertiser_details']
                print(f"\n🏢 ADVERTISER PAGE DETAILS:")
                print(f"  Page Visited: {'✅ YES' if details.get('page_visited') else '❌ NO'}")
                
                if details.get('page_visited'):
                    if details.get('emails'):
                        print(f"  📧 Emails Found: {', '.join(details['emails'])}")
                    else:
                        print(f"  📧 Emails Found: None")
                    
                    if details.get('phones'):
                        print(f"  📞 Phones Found: {', '.join(details['phones'][:3])}")
                    else:
                        print(f"  📞 Phones Found: None")
                    
                    if details.get('websites'):
                        print(f"  🌐 Websites: {', '.join(details['websites'][:2])}")
                    
                    if details.get('facebook_page'):
                        print(f"  👥 Facebook: {details['facebook_page'][:60]}...")
                    
                    if details.get('instagram'):
                        print(f"  📸 Instagram: {details['instagram'][:60]}...")
                    
                    if details.get('bio'):
                        print(f"  📄 Bio: {details['bio'][:150]}...")
                
                if details.get('error'):
                    print(f"  ⚠️  Error: {details['error']}")
        
        # Save results
        with open('debug_results.json', 'w', encoding='utf-8') as f:
            json.dump(ads, f, ensure_ascii=False, indent=2)
        print(f"\n\n✅ Full results saved to: debug_results.json")
        
        # Summary of contact info found
        print(f"\n{'='*80}")
        print("📊 CONTACT INFO SUMMARY:")
        print(f"{'='*80}")
        
        total_emails = set()
        total_phones = set()
        total_websites = set()
        ads_with_contact = 0
        
        for ad in real_ads:
            has_contact = False
            
            if ad.get('ad_emails'):
                total_emails.update(ad['ad_emails'])
                has_contact = True
            if ad.get('ad_phones'):
                total_phones.update(ad['ad_phones'])
                has_contact = True
            if ad.get('ad_websites'):
                total_websites.update(ad['ad_websites'])
                has_contact = True
            
            if 'advertiser_details' in ad:
                details = ad['advertiser_details']
                if details.get('emails'):
                    total_emails.update(details['emails'])
                    has_contact = True
                if details.get('phones'):
                    total_phones.update(details['phones'])
                    has_contact = True
                if details.get('websites'):
                    total_websites.update(details['websites'])
                    has_contact = True
            
            if has_contact:
                ads_with_contact += 1
        
        print(f"Ads with contact info: {ads_with_contact}/{len(real_ads)}")
        print(f"Unique emails found: {len(total_emails)}")
        print(f"Unique phones found: {len(total_phones)}")
        print(f"Unique websites found: {len(total_websites)}")
        
        if total_emails:
            print(f"\n📧 Sample emails: {', '.join(list(total_emails)[:5])}")
        if total_phones:
            print(f"📞 Sample phones: {', '.join(list(total_phones)[:5])}")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_debug_scraper())